from __future__ import annotations

import re

from .models import Company, Decision, Signup


def score(signup: Signup, company: Company, icp: dict) -> tuple[int, list[str]]:
    """Points-based fit score against the ideal customer profile in config.

    Every point comes with a reason string, so a rep (or a reviewer) can see why
    a lead landed where it did.
    """
    pts, reasons = 0, []
    if company.personal_email:
        pts += icp.get("personal_email_points", -20)
        reasons.append("personal email domain")

    emp = company.employees
    for band in icp.get("employee_bands", []):
        lo, hi = band.get("min", 0), band.get("max")
        if emp is not None and emp >= lo and (hi is None or emp <= hi):
            pts += band["points"]
            reasons.append(f"{emp} employees ({band['label']})")
            break

    industry = company.industry.lower()
    for ind in icp.get("target_industries", []):
        if ind.lower() in industry:
            pts += icp.get("industry_points", 20)
            reasons.append(f"target industry: {company.industry}")
            break

    title = signup.title.lower()
    for rule in icp.get("title_rules", []):
        if re.search(rule["pattern"], title):
            pts += rule["points"]
            reasons.append(f"title matches {rule['label']}")
            break

    if company.country and company.country.upper() in {c.upper() for c in icp.get("target_countries", [])}:
        pts += icp.get("country_points", 10)
        reasons.append(f"target country: {company.country}")

    return max(0, min(100, pts)), reasons


class Router:
    """Turns a score into a queue and an owner.

    Tier A goes to account executives, tier B to SDRs, both round-robin so load
    stays even. Tier C goes to nurture with no owner and no task. The round-robin
    position is persisted by the caller via `state` so restarts don't reset it.
    """

    def __init__(self, routing_cfg: dict, state: dict | None = None):
        self.cfg = routing_cfg
        self.state = state if state is not None else {}

    def tier(self, pts: int) -> str:
        if pts >= self.cfg["tier_a_min"]:
            return "A"
        if pts >= self.cfg["tier_b_min"]:
            return "B"
        return "C"

    def next_owner(self, queue: str) -> str | None:
        owners = self.cfg.get("queues", {}).get(queue, [])
        if not owners:
            return None
        i = self.state.get(queue, 0)
        self.state[queue] = i + 1
        return owners[i % len(owners)]

    def decide(self, pts: int, reasons: list[str]) -> Decision:
        t = self.tier(pts)
        queue = {"A": "ae", "B": "sdr", "C": "nurture"}[t]
        owner = self.next_owner(queue) if queue != "nurture" else None
        return Decision(
            score=pts, tier=t, reasons=reasons,
            owner_email=owner, queue=queue, create_task=owner is not None,
        )
