from __future__ import annotations

from dataclasses import dataclass, field

from .hubspot import HubSpot
from .store import Store


@dataclass
class Report:
    checked: int = 0
    missing: list[str] = field(default_factory=list)  # in our log, gone from HubSpot
    owner_drift: list[tuple[str, str | None, str | None]] = field(default_factory=list)
    tier_drift: list[tuple[str, str | None, str | None]] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.missing or self.owner_drift or self.tier_drift)

    def markdown(self) -> str:
        lines = [f"# Nightly reconciliation", f"Checked {self.checked} synced contacts."]
        if self.clean:
            lines.append("Everything matches HubSpot.")
            return "\n".join(lines)
        if self.missing:
            lines.append(f"\n## Missing from HubSpot ({len(self.missing)})")
            lines += [f"- {e}" for e in self.missing]
        if self.owner_drift:
            lines.append(f"\n## Owner changed in HubSpot ({len(self.owner_drift)})")
            lines += [f"- {e}: expected owner id {a}, HubSpot has {b}" for e, a, b in self.owner_drift]
        if self.tier_drift:
            lines.append(f"\n## Tier changed in HubSpot ({len(self.tier_drift)})")
            lines += [f"- {e}: expected {a}, HubSpot has {b}" for e, a, b in self.tier_drift]
        return "\n".join(lines)

    def summary(self) -> str:
        if self.clean:
            return f"Reconciliation: {self.checked} contacts checked, all match HubSpot."
        return (
            f"Reconciliation: {self.checked} checked, {len(self.missing)} missing, "
            f"{len(self.owner_drift)} owner changes, {len(self.tier_drift)} tier changes."
        )


def reconcile(store: Store, hs: HubSpot) -> Report:
    """Compare what we synced with what HubSpot holds now.

    Catches records deleted or merged in HubSpot, and owners or tiers that someone
    changed by hand. Drift isn't always wrong (a manager can reassign a lead on
    purpose), so this reports and doesn't overwrite.
    """
    rows = [r for r in store.synced() if r["hubspot_contact_id"]]
    rep = Report(checked=len(rows))
    if not rows:
        return rep
    found = hs.read_contacts(
        [r["hubspot_contact_id"] for r in rows], ["email", "hubspot_owner_id", "signal_tier"]
    )
    for r in rows:
        props = found.get(str(r["hubspot_contact_id"]))
        if props is None:
            rep.missing.append(r["email"])
            continue
        expected_owner = hs.owner_id(r["owner_email"])
        actual_owner = props.get("hubspot_owner_id") or None
        if (expected_owner or None) != actual_owner:
            rep.owner_drift.append((r["email"], expected_owner, actual_owner))
        if (r["tier"] or None) != (props.get("signal_tier") or None):
            rep.tier_drift.append((r["email"], r["tier"], props.get("signal_tier")))
    return rep
