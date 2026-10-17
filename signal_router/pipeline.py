from __future__ import annotations

from dataclasses import dataclass

from .enrich import Enricher
from .hubspot import HubSpot
from .models import Company, Decision, Signup, fingerprint
from .routing import Router, score
from .slack import Slack, lead_alert
from .store import Store


@dataclass
class Result:
    signup_id: str
    action: str  # created, updated, skipped
    tier: str
    owner: str | None
    contact_id: str | None = None


class Pipeline:
    """End to end for one signup: enrich -> score -> route -> HubSpot -> task -> Slack.

    Safe to re-run. A signup whose data and routing haven't changed since its last
    sync is skipped, and ownership is sticky: once a lead has an owner it keeps
    them on re-runs instead of advancing the round-robin.
    """

    def __init__(self, store: Store, enricher: Enricher, router: Router, hubspot: HubSpot | None, slack: Slack, icp: dict):
        self.store = store
        self.enricher = enricher
        self.router = router
        self.hs = hubspot
        self.slack = slack
        self.icp = icp

    def _decide(self, s: Signup, c: Company) -> Decision:
        pts, reasons = score(s, c, self.icp)
        prev = self.store.last_sync(s.id)
        tier = self.router.tier(pts)
        if prev and prev["owner_email"] and prev["tier"] == tier:
            # sticky owner: same tier as last time, keep who already has it
            return Decision(
                score=pts, tier=tier, reasons=reasons, owner_email=prev["owner_email"],
                queue={"A": "ae", "B": "sdr", "C": "nurture"}[tier], create_task=False,
            )
        d = self.router.decide(pts, reasons)
        if prev and prev["hubspot_task_id"]:
            d.create_task = False  # already has a follow-up task; don't stack another
        return d

    def process(self, s: Signup, dry_run: bool = False) -> Result:
        c = self.enricher.company_for(s.domain, fallback_name=s.company)
        d = self._decide(s, c)
        fp = fingerprint(s, c, d.score, d.tier, d.owner_email)
        prev = self.store.last_sync(s.id)
        if prev and prev["fingerprint"] == fp:
            return Result(s.id, "skipped", d.tier, d.owner_email, prev["hubspot_contact_id"])

        if dry_run or self.hs is None:
            self.slack.send(lead_alert(s, c, d))
            return Result(s.id, "dry-run", d.tier, d.owner_email)

        company_id = None
        if not c.personal_email:
            company_id = self.hs.upsert_company(
                c.domain,
                {"name": c.name, "numberofemployees": c.employees, "country": c.country},
            )
        owner_id = self.hs.owner_id(d.owner_email)
        contact_id, created = self.hs.upsert_contact(
            s.email,
            {
                "firstname": s.first_name,
                "lastname": s.last_name,
                "jobtitle": s.title,
                "company": c.name,
                "signal_score": d.score,
                "signal_tier": d.tier,
                "hubspot_owner_id": owner_id,
            },
        )
        if company_id:
            self.hs.associate_contact_company(contact_id, company_id)

        task_id = prev["hubspot_task_id"] if prev else None
        if d.create_task:
            task_id = self.hs.create_task(
                contact_id, owner_id,
                subject=f"Follow up: tier {d.tier} signup from {c.name or c.domain}",
                body="Why this lead: " + "; ".join(d.reasons),
            )
        self.store.record_sync(
            signup_id=s.id, email=s.email.lower(), hubspot_contact_id=contact_id,
            hubspot_company_id=company_id, hubspot_task_id=task_id,
            owner_email=d.owner_email, tier=d.tier, score=d.score, fingerprint=fp,
        )
        # Alert when a lead first becomes worth a rep's time: new A/B, or upgraded from C.
        if d.tier in ("A", "B") and (not prev or prev["tier"] not in ("A", "B")):
            self.slack.send(lead_alert(s, c, d))
        return Result(s.id, "created" if created else "updated", d.tier, d.owner_email, contact_id)

    def run(self, signups: list[Signup], dry_run: bool = False) -> list[Result]:
        results = [self.process(s, dry_run=dry_run) for s in signups]
        if not dry_run:
            self.store.save_router_state(self.router.state)
        return results
