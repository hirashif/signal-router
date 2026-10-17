from __future__ import annotations

from .http import Transport, call
from .models import Company, Decision, Signup


class Slack:
    """Posts to an incoming webhook. With no webhook configured it prints instead."""

    def __init__(self, webhook_url: str | None, transport: Transport | None = None, out=print):
        self.url = webhook_url
        self.t = transport
        self.out = out

    def send(self, text: str) -> None:
        if not self.url:
            self.out(f"[slack dry-run] {text}")
            return
        call(self.t, "POST", self.url, json={"text": text})


def lead_alert(s: Signup, c: Company, d: Decision) -> str:
    who = f"{s.first_name} {s.last_name}".strip() or s.email
    size = f"{c.employees} employees" if c.employees else "size unknown"
    owner = d.owner_email or "nurture (no owner)"
    why = "; ".join(d.reasons) or "no fit signals"
    return (
        f"New tier {d.tier} signup ({d.score}/100): {who}, {s.title or 'no title'} at "
        f"{c.name or ('a personal email' if c.personal_email else c.domain)} ({size}). Routed to {owner}. Why: {why}."
    )
