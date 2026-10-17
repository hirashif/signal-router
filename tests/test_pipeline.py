import pytest
import yaml

from signal_router.enrich import Enricher, HunterProvider, parse_employees
from signal_router.http import HttpError, call
from signal_router.hubspot import HubSpot
from signal_router.models import Signup
from signal_router.pipeline import Pipeline
from signal_router.reconcile import reconcile
from signal_router.routing import Router, score
from signal_router.slack import Slack
from signal_router.store import Store

from .fakes import FakeAPI

CFG = yaml.safe_load(open("config.example.yaml"))

HUNTER = {
    "stripe.com": {"name": "Stripe", "category": {"industry": "Financial Services"}, "metrics": {"employees": "5K+"}, "geo": {"country": "US"}},
    "hubspot.com": {"name": "HubSpot", "category": {"industry": "Software"}, "metrics": {"employees": "1001-5000"}, "geo": {"country": "US"}},
    "ramp.com": {"name": "Ramp", "category": {"industry": "Financial Services"}, "metrics": {"employees": "501-1000"}, "geo": {"country": "US"}},
}

VP = Signup("s-001", "sample.vp@stripe.com", "Maria", "Chen", "VP Revenue Operations", "Stripe")
STUDENT = Signup("s-002", "sample.student@gmail.com", "Dev", "Patel", "Student")
OPS = Signup("s-003", "sample.salesops@hubspot.com", "Jordan", "Lee", "Sales Operations Manager", "HubSpot")
ENG = Signup("s-004", "sample.engineer@ramp.com", "Alex", "Kim", "Software Engineer", "Ramp")


def make(api=None, db=":memory:"):
    api = api or FakeAPI(companies_by_domain=HUNTER)
    store = Store(db)
    hs = HubSpot("test-token", api)
    hs.ensure_custom_properties()
    pipe = Pipeline(
        store,
        Enricher([HunterProvider("k", api)], store),
        Router(CFG["routing"], store.router_state()),
        hs,
        Slack("https://hooks.slack.com/services/T/B/X", api),
        CFG["icp"],
    )
    return api, store, hs, pipe


def test_parse_employees_keeps_lower_bound():
    assert parse_employees("51-200") == 51
    assert parse_employees("5K+") == 5000
    assert parse_employees(1200) == 1200
    assert parse_employees("") is None


def test_scoring_explains_every_point():
    api, store, hs, pipe = make()
    c = pipe.enricher.company_for("stripe.com")
    pts, reasons = score(VP, c, CFG["icp"])
    assert pts >= CFG["routing"]["tier_a_min"]
    assert any("exec" in r for r in reasons)
    assert any("Financial" in r for r in reasons)


def test_personal_email_skips_providers_and_goes_to_nurture():
    api, store, hs, pipe = make()
    res = pipe.run([STUDENT])[0]
    assert res.tier == "C" and res.owner is None
    assert not any("hunter.io" in url for _, url in api.calls)
    assert api.objects["tasks"] == {}
    assert api.objects["companies"] == {}  # no company record for gmail.com


def test_tier_a_creates_contact_company_task_and_alert():
    api, store, hs, pipe = make()
    res = pipe.run([VP])[0]
    assert res.action == "created" and res.tier == "A"
    contact = api.objects["contacts"][res.contact_id]
    assert contact["signal_tier"] == "A"
    assert contact["hubspot_owner_id"] == "1"  # first AE in round-robin
    assert len(api.objects["companies"]) == 1 and len(api.associations) == 1
    assert len(api.objects["tasks"]) == 1
    assert len(api.slack_messages) == 1 and "tier A" in api.slack_messages[0]


def test_rerun_is_idempotent():
    api, store, hs, pipe = make()
    pipe.run([VP, OPS])
    writes_before = [c for c in api.calls if c[0] in ("POST", "PATCH", "PUT") and "search" not in c[1]]
    second = pipe.run([VP, OPS])
    writes_after = [c for c in api.calls if c[0] in ("POST", "PATCH", "PUT") and "search" not in c[1]]
    assert [r.action for r in second] == ["skipped", "skipped"]
    assert writes_after == writes_before
    assert len(api.objects["contacts"]) == 2 and len(api.objects["tasks"]) == 2


def test_round_robin_spreads_tier_a_and_persists(tmp_path):
    db = str(tmp_path / "rr.db")
    api, store, hs, pipe = make(db=db)
    a1 = pipe.run([VP])[0]
    # new process, same db: round-robin continues instead of restarting at AE one
    api2, store2, hs2, pipe2 = make(api=api, db=db)
    a2 = pipe2.run([Signup("s-010", "sample.cto@ramp.com", "Pat", "Ng", "CTO", "Ramp")])[0]
    assert a1.owner == "ae-one@example.com"
    assert a2.owner == "ae-two@example.com"


def test_existing_contact_in_hubspot_is_updated_not_duplicated():
    api, store, hs, pipe = make()
    hs.upsert_contact("sample.vp@stripe.com", {"firstname": "Maria"})  # created by someone else
    res = pipe.run([VP])[0]
    assert res.action == "updated"
    assert len(api.objects["contacts"]) == 1


def test_company_deduped_by_domain():
    api, store, hs, pipe = make()
    pipe.run([ENG, Signup("s-011", "sample.cfo@ramp.com", "Lee", "Park", "CFO", "Ramp")])
    assert len(api.objects["companies"]) == 1
    assert len(api.associations) == 2


def test_retries_429_then_succeeds():
    api = FakeAPI()
    api.fail_next = [429, 503]
    slept = []
    body = call(api, "GET", "https://api.hubapi.com/crm/v3/owners", sleep=slept.append)
    assert body["results"] and len(slept) == 2


def test_does_not_retry_bad_request():
    api = FakeAPI()
    api.fail_next = [400]
    with pytest.raises(HttpError):
        call(api, "GET", "https://api.hubapi.com/crm/v3/owners", sleep=lambda s: None)
    assert len(api.calls) == 1


def test_unknown_domain_falls_back_and_is_cached():
    api, store, hs, pipe = make()
    c1 = pipe.enricher.company_for("tinyshop.io", fallback_name="Tiny Shop")
    c2 = pipe.enricher.company_for("tinyshop.io")
    assert c1.provider == "fallback" and c1.name == "Tiny Shop"
    assert c2 == c1
    assert sum("hunter.io" in url for _, url in api.calls) == 1


def test_reconcile_reports_deletes_and_manual_reassignments():
    api, store, hs, pipe = make()
    r_vp, r_ops = pipe.run([VP, OPS])
    assert reconcile(store, hs).clean
    del api.objects["contacts"][r_vp.contact_id]                      # merged or deleted in HubSpot
    api.objects["contacts"][r_ops.contact_id]["hubspot_owner_id"] = "3"  # reassigned by a manager
    rep = reconcile(store, hs)
    assert rep.missing == ["sample.vp@stripe.com"]
    assert [e for e, _, _ in rep.owner_drift] == ["sample.salesops@hubspot.com"]
    assert "1 missing" in rep.summary()
