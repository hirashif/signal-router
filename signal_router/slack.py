from __future__ import annotations

from .http import Transport, call
from .models import Company, Decision, Signup


class Notifier:
    """Posts alerts to a Slack or Discord incoming webhook.

    The payload shape differs ({"text"} for Slack, {"content"} for Discord), so it's
    picked from the URL. With no webhook configured it prints instead.
    """

    def __init__(self, webhook_url: str | None, transport: Transport | None = None, out=print):
        self.url = webhook_url
        self.t = transport
        self.out = out

    @property
    def kind(self) -> str:
        if not self.url:
            return "none"
        if "discord.com/api/webhooks" in self.url or "discordapp.com/api/webhooks" in self.url:
            return "discord"
        return "slack"

    def send(self, text: str) -> None:
        if self.kind == "none":
            self.out(f"[alert dry-run] {text}")
            return
        payload = {"content": text[:2000]} if self.kind == "discord" else {"text": text}
        call(self.t, "POST", self.url, json=payload)


# Kept so existing imports keep working.
Slack = Notifier


def lead_alert(s: Signup, c: Company, d: Decision) -> str:
    who = f"{s.first_name} {s.last_name}".strip() or s.email
    size = f"{c.employees} employees" if c.employees else "size unknown"
    owner = d.owner_email or "nurture (no owner)"
    why = "; ".join(d.reasons) or "no fit signals"
    return (
        f"New tier {d.tier} signup ({d.score}/100): {who}, {s.title or 'no title'} at "
        f"{c.name or ('a personal email' if c.personal_email else c.domain)} ({size}). Routed to {owner}. Why: {why}."
    )
