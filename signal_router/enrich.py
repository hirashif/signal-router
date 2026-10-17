from __future__ import annotations

import re
from typing import Protocol

from .http import HttpError, Transport, call
from .models import Company

PERSONAL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com",
    "live.com", "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com",
    "gmx.com", "zoho.com", "yandex.com", "mail.com",
}


def parse_employees(value) -> int | None:
    """Providers report headcount as an int, '51-200', '1000+', or '10K+'.

    We keep the lower bound: scoring should not promote a company on a guess.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = re.match(r"\s*([\d,.]+)\s*([kK])?", str(value))
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    if m.group(2):
        n *= 1000
    return int(n)


class Provider(Protocol):
    name: str

    def lookup(self, domain: str) -> Company | None: ...


class HunterProvider:
    """Hunter Company Enrichment: https://api.hunter.io/v2/companies/find"""

    name = "hunter"

    def __init__(self, api_key: str, transport: Transport):
        self.api_key = api_key
        self.t = transport

    def lookup(self, domain: str) -> Company | None:
        try:
            body = call(
                self.t, "GET", "https://api.hunter.io/v2/companies/find",
                params={"domain": domain, "api_key": self.api_key},
            )
        except HttpError as e:
            if e.status in (400, 404):
                return None
            raise
        d = (body or {}).get("data") or {}
        if not d.get("name"):
            return None
        return Company(
            domain=domain,
            name=d.get("name", ""),
            industry=((d.get("category") or {}).get("industry") or ""),
            employees=parse_employees((d.get("metrics") or {}).get("employees")),
            country=((d.get("geo") or {}).get("country") or ""),
            provider=self.name,
        )


class PDLProvider:
    """People Data Labs Company Enrichment (free tier available)."""

    name = "pdl"

    def __init__(self, api_key: str, transport: Transport):
        self.api_key = api_key
        self.t = transport

    def lookup(self, domain: str) -> Company | None:
        try:
            body = call(
                self.t, "GET", "https://api.peopledatalabs.com/v5/company/enrich",
                params={"website": domain}, headers={"X-Api-Key": self.api_key},
            )
        except HttpError as e:
            if e.status in (400, 404):
                return None
            raise
        if not body or not body.get("name"):
            return None
        return Company(
            domain=domain,
            name=body.get("display_name") or body.get("name", ""),
            industry=body.get("industry") or "",
            employees=parse_employees(body.get("employee_count") or body.get("size")),
            country=((body.get("location") or {}).get("country") or ""),
            provider=self.name,
        )


class Enricher:
    """Waterfall: cache first, then each provider in order until one answers.

    Personal email domains skip the paid providers entirely (no company to find),
    and a domain no provider knows still gets a minimal record so routing works.
    """

    def __init__(self, providers: list[Provider], store=None):
        self.providers = providers
        self.store = store

    def company_for(self, domain: str, fallback_name: str = "") -> Company:
        domain = domain.lower()
        if domain in PERSONAL_DOMAINS:
            return Company(domain=domain, name=fallback_name, provider="personal", personal_email=True)
        if self.store:
            cached = self.store.cached_company(domain)
            if cached:
                return cached
        found = None
        for p in self.providers:
            try:
                found = p.lookup(domain)
            except HttpError:
                found = None  # one provider being down shouldn't stop the waterfall
            if found:
                break
        if not found:
            found = Company(domain=domain, name=fallback_name or domain.split(".")[0].title(), provider="fallback")
        if self.store:
            self.store.cache_company(found)
        return found
