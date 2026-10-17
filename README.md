# signal-router

Routes new signups into HubSpot the way a sales team would want them: enriched, scored, owned, and followed up.

```
signup CSV ─▶ enrich (cache ▸ Hunter ▸ People Data Labs ▸ fallback)
            ─▶ score against the ICP (every point has a reason)
            ─▶ route: tier A ▸ AEs, tier B ▸ SDRs (round-robin), tier C ▸ nurture
            ─▶ HubSpot: upsert company (by domain) + contact (by email), associate,
               set owner + signal_score + signal_tier, create the owner's follow-up task
            ─▶ Slack alert for A/B leads
nightly    ─▶ reconcile: what we synced vs. what HubSpot holds now
```

## Why it's built this way

- **Re-runs are safe.** Each signup's synced state is fingerprinted. If nothing changed, it's skipped: no duplicate contacts, tasks, or alerts.
- **Ownership is sticky.** Once a lead has an owner, a re-run keeps that owner instead of advancing the round-robin. The round-robin position is stored, so a restart doesn't send the next five leads to the same rep.
- **Dedupe matches how HubSpot thinks.** Contacts are keyed on email and companies on domain, and a contact someone already created by hand gets updated, not duplicated.
- **Enrichment is a waterfall with a cache.** Personal email domains never hit a paid provider. If one provider is down or doesn't know the domain, the next one is tried. A domain nobody knows still gets a minimal record so routing works.
- **Rate limits are expected.** 429s and 5xx retry with backoff, honoring `Retry-After`. Other 4xx errors fail fast.
- **Reconciliation reports, it doesn't overwrite.** A manager reassigning a lead on purpose shows up as drift for someone to look at, not something to undo.

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env              # HUBSPOT_TOKEN (private app), optional HUNTER_API_KEY, PDL_API_KEY, SLACK_WEBHOOK_URL
cp config.example.yaml config.yaml  # ICP weights, tiers, and owner emails (must be HubSpot users)

.venv/bin/signal-router run sample_data/signups.csv --dry-run   # no HubSpot writes
.venv/bin/signal-router setup                                    # creates signal_score / signal_tier properties
.venv/bin/signal-router run sample_data/signups.csv
.venv/bin/signal-router reconcile
.venv/bin/pytest -q
```

HubSpot private app scopes: contacts, companies and owners (read/write), plus contact schemas (to create the two custom properties).

## Tests

The tests run against an in-memory fake of the HubSpot, Hunter, and Slack APIs, so they cover the full pipeline without network calls: idempotent re-runs, round-robin persistence across restarts, dedupe on email and domain, retry behavior, enrichment fallback and caching, and reconciliation catching deleted records and manual reassignments.
