from __future__ import annotations

import argparse
import csv
import os
import sys

from .config import load_config, load_env
from .enrich import Enricher, HunterProvider, PDLProvider
from .http import default_transport
from .hubspot import HubSpot
from .models import Signup
from .pipeline import Pipeline
from .reconcile import reconcile
from .routing import Router
from .slack import Slack
from .store import Store


def read_signups(path: str) -> list[Signup]:
    with open(path, newline="") as f:
        return [Signup(**{k: (v or "").strip() for k, v in row.items() if k in Signup.__dataclass_fields__}) for row in csv.DictReader(f)]


def build(cfg: dict, db: str, need_hubspot: bool):
    t = default_transport()
    store = Store(db)
    providers = []
    if os.getenv("HUNTER_API_KEY"):
        providers.append(HunterProvider(os.environ["HUNTER_API_KEY"], t))
    if os.getenv("PDL_API_KEY"):
        providers.append(PDLProvider(os.environ["PDL_API_KEY"], t))
    token = os.getenv("HUBSPOT_TOKEN")
    if need_hubspot and not token:
        sys.exit("HUBSPOT_TOKEN is not set (put it in .env). Use --dry-run to try without HubSpot.")
    hs = HubSpot(token, t) if token else None
    slack = Slack(os.getenv("DISCORD_WEBHOOK_URL") or os.getenv("SLACK_WEBHOOK_URL"), t)
    return store, Enricher(providers, store), hs, slack


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="signal-router")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--db", default="signal_router.db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="create the custom HubSpot contact properties")
    r = sub.add_parser("run", help="ingest a CSV of signups and route them")
    r.add_argument("csv")
    r.add_argument("--dry-run", action="store_true", help="enrich, score and route, but don't write to HubSpot")
    sub.add_parser("reconcile", help="compare synced records against HubSpot")
    args = ap.parse_args(argv)

    load_env()
    cfg = load_config(args.config)

    if args.cmd == "setup":
        _, _, hs, _ = build(cfg, args.db, need_hubspot=True)
        created = hs.ensure_custom_properties()
        print("created properties: " + (", ".join(created) or "none (already there)"))
        return

    if args.cmd == "run":
        store, enricher, hs, slack = build(cfg, args.db, need_hubspot=not args.dry_run)
        new = sum(store.add_signup(s) for s in read_signups(args.csv))
        router = Router(cfg["routing"], store.router_state())
        pipe = Pipeline(store, enricher, router, hs, slack, cfg["icp"])
        results = pipe.run(store.signups(), dry_run=args.dry_run)
        print(f"{new} new signups ingested")
        for res in results:
            print(f"  {res.signup_id:<8} {res.action:<8} tier {res.tier}  owner={res.owner or '-'}")
        return

    if args.cmd == "reconcile":
        store, _, hs, slack = build(cfg, args.db, need_hubspot=True)
        rep = reconcile(store, hs)
        print(rep.markdown())
        slack.send(rep.summary())
        return


if __name__ == "__main__":
    main()
