from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field


@dataclass
class Signup:
    """One inbound signup, as it arrives from the product or a form."""

    id: str
    email: str
    first_name: str = ""
    last_name: str = ""
    title: str = ""
    company: str = ""
    source: str = ""

    @property
    def domain(self) -> str:
        return self.email.rsplit("@", 1)[-1].strip().lower()


@dataclass
class Company:
    """What we know about the signup's company after enrichment."""

    domain: str
    name: str = ""
    industry: str = ""
    employees: int | None = None
    country: str = ""
    provider: str = ""  # which provider in the waterfall answered
    personal_email: bool = False


@dataclass
class Decision:
    """Scoring and routing outcome for one signup."""

    score: int
    tier: str  # A, B or C
    reasons: list[str] = field(default_factory=list)
    owner_email: str | None = None
    queue: str = ""  # ae, sdr or nurture
    create_task: bool = False


def fingerprint(*parts: object) -> str:
    """Stable hash of everything we would send to HubSpot.

    Used to skip re-syncing a signup when nothing about it changed, so re-running
    the pipeline is safe.
    """
    blob = json.dumps(
        [asdict(p) if hasattr(p, "__dataclass_fields__") else p for p in parts],
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]
