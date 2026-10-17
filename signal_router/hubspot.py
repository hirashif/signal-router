from __future__ import annotations

import time
from typing import Any

from .http import Transport, call

BASE = "https://api.hubapi.com"
TASK_TO_CONTACT = 204  # HubSpot-defined association type: task -> contact

CUSTOM_PROPERTIES = [
    {
        "name": "signal_score",
        "label": "Signal score",
        "type": "number",
        "fieldType": "number",
        "groupName": "contactinformation",
        "description": "Fit score (0-100) set by signal-router",
    },
    {
        "name": "signal_tier",
        "label": "Signal tier",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "contactinformation",
        "description": "A / B / C tier set by signal-router",
        "options": [
            {"label": "A", "value": "A"},
            {"label": "B", "value": "B"},
            {"label": "C", "value": "C"},
        ],
    },
]


class HubSpot:
    """Thin client over the HubSpot CRM v3/v4 APIs, private-app token auth."""

    def __init__(self, token: str, transport: Transport):
        self.t = transport
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        self._owner_ids: dict[str, str] | None = None

    def _call(self, method: str, path: str, **kw: Any) -> Any:
        return call(self.t, method, BASE + path, headers=self.headers, **kw)

    # setup -------------------------------------------------------------
    def ensure_custom_properties(self) -> list[str]:
        """Create signal_score / signal_tier on contacts if missing. Returns created names."""
        existing = {
            p["name"] for p in self._call("GET", "/crm/v3/properties/contacts").get("results", [])
        }
        created = []
        for prop in CUSTOM_PROPERTIES:
            if prop["name"] not in existing:
                self._call("POST", "/crm/v3/properties/contacts", json=prop)
                created.append(prop["name"])
        return created

    # owners ------------------------------------------------------------
    def owner_id(self, email: str | None) -> str | None:
        if not email:
            return None
        if self._owner_ids is None:
            res = self._call("GET", "/crm/v3/owners", params={"limit": 100})
            self._owner_ids = {o["email"].lower(): str(o["id"]) for o in res.get("results", [])}
        return self._owner_ids.get(email.lower())

    # search helpers ----------------------------------------------------
    def _find_one(self, obj: str, prop: str, value: str) -> dict | None:
        body = {
            "filterGroups": [{"filters": [{"propertyName": prop, "operator": "EQ", "value": value}]}],
            "limit": 1,
        }
        res = self._call("POST", f"/crm/v3/objects/{obj}/search", json=body)
        hits = res.get("results", [])
        return hits[0] if hits else None

    # companies ---------------------------------------------------------
    def upsert_company(self, domain: str, props: dict) -> str:
        """Dedupe companies on domain: one HubSpot company per domain."""
        found = self._find_one("companies", "domain", domain)
        clean = {k: v for k, v in props.items() if v not in (None, "")}
        if found:
            if clean:
                self._call("PATCH", f"/crm/v3/objects/companies/{found['id']}", json={"properties": clean})
            return str(found["id"])
        res = self._call("POST", "/crm/v3/objects/companies", json={"properties": {"domain": domain, **clean}})
        return str(res["id"])

    # contacts ----------------------------------------------------------
    def upsert_contact(self, email: str, props: dict) -> tuple[str, bool]:
        """Dedupe contacts on email. Returns (id, created)."""
        email = email.lower()
        found = self._find_one("contacts", "email", email)
        clean = {k: v for k, v in props.items() if v not in (None, "")}
        if found:
            self._call("PATCH", f"/crm/v3/objects/contacts/{found['id']}", json={"properties": clean})
            return str(found["id"]), False
        res = self._call("POST", "/crm/v3/objects/contacts", json={"properties": {"email": email, **clean}})
        return str(res["id"]), True

    def associate_contact_company(self, contact_id: str, company_id: str) -> None:
        self._call(
            "PUT",
            f"/crm/v4/objects/contacts/{contact_id}/associations/default/companies/{company_id}",
        )

    def read_contacts(self, ids: list[str], properties: list[str]) -> dict[str, dict]:
        """Batch read, 100 per call. Returns {id: properties}; missing ids are absent."""
        out: dict[str, dict] = {}
        for i in range(0, len(ids), 100):
            chunk = ids[i : i + 100]
            res = self._call(
                "POST", "/crm/v3/objects/contacts/batch/read",
                json={"inputs": [{"id": x} for x in chunk], "properties": properties},
            )
            for r in res.get("results", []):
                out[str(r["id"])] = r.get("properties", {})
        return out

    # tasks -------------------------------------------------------------
    def create_task(self, contact_id: str, owner_id: str | None, subject: str, body: str, due_ms: int | None = None) -> str:
        props = {
            "hs_task_subject": subject,
            "hs_task_body": body,
            "hs_task_status": "NOT_STARTED",
            "hs_task_priority": "HIGH",
            "hs_timestamp": str(due_ms or int(time.time() * 1000) + 24 * 3600 * 1000),
        }
        if owner_id:
            props["hubspot_owner_id"] = owner_id
        res = self._call(
            "POST", "/crm/v3/objects/tasks",
            json={
                "properties": props,
                "associations": [{
                    "to": {"id": contact_id},
                    "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": TASK_TO_CONTACT}],
                }],
            },
        )
        return str(res["id"])
