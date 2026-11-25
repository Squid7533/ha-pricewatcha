# Roadmap 0.8.0 and 0.9.0 — free-source deal engine and universal tracker

Status: 0.8.0 and 0.9.0 implemented in v0.8.0/v0.9.0.

Baseline: 0.7.0, 162 tests; 0.8.0 adds 9 (171 total), 0.9.0 adds 11 (182 total); 0.10.0 simplifies the surface (186 total).

## Goal

Turn `/deal` from a Pricewatcha-centric report into a **source-agnostic deal engine**,
then give external offers the same tracking, history and alerting that catalog products
already have.

Hard constraint: **no paid APIs and no new accounts.** The existing Pricewatcha key and
the existing DuckDuckGo/SearXNG search setting are the only credentials.

Non-goals:

- no eBay Buy API / Amazon PA-API (partner or affiliate approval required),
- no idealo MCP account,
- no Kleinanzeigen/Vinted scraping in the default path,
- no paid Piloterr/PricePirate integration,
- no purchase automation.

## Principles

1. Public pages and existing APIs only; respect per-source rate limits and timeouts.
2. A failed source must never break the reply: partial results, explicit source status.
3. Total price (`price + shipping`) is the comparison basis; unknown shipping is labeled,
   never treated as zero.
4. Conditions are never mixed: best new, best refurbished, best used, best overall.
5. External data is labeled unverified and kept separate from catalog facts.
6. Cache and deduplicate before adding more sources.

---

# 0.8.0 — Free-source deal engine

## Objective

A normalized `Offer` layer with pluggable free sources, deduplication and condition-aware
ranking. `/deal` returns the best offers across Pricewatcha, direct shops, idealo and
Geizhals, with a clear source and total price per offer.

## Work packages

### WP 1 — Offer model and source contract

New `custom_components/pricewatcha/offers.py`:

```text
Offer
  source: str                 pricewatcha | web | idealo | geizhals
  source_id: str | None
  product_key: str            GTIN if available, else normalized title + variant
  title: str
  url: str
  shop: str
  seller: str | None
  price: float | None
  shipping: float | None
  total: float | None          computed when shipping is known
  currency: str
  condition: str               new | refurbished | used | unknown
  availability: str | None
  observed_at: datetime
  trackable: bool
  verified: bool               page JSON-LD/API vs snippet
```

`OfferSet` helpers:

- `add()` with dedupe key `(product_key, shop, condition)`,
- `best_by_condition()`,
- `cheapest_total()`,
- `merge()` for source results,
- `to_dict()` for Telegram/AI payloads.

`OfferSource` protocol:

```text
name: str
async search(query, context) -> list[Offer]
supports_tracking: bool
```

All sources get a per-call timeout (default 8 s) and a result cap.

### WP 2 — Source adapters

| Adapter | Implementation | Notes |
| --- | --- | --- |
| `PricewatchaSource` | wraps the existing catalog search and product details | no duplicate API calls; produces normalized catalog offers |
| `WebShopSource` | existing DDG/SearXNG search + direct-page JSON-LD | expand from 3 to 6 direct results; condition, availability and shipping where stated |
| `IdealoSource` | existing `parse_idealo` aggregate | new low + B-Ware/used low; not trackable |
| `GeizhalsSource` | new parser for Geizhals product page | price and offer count from visible/structured data; test before promising used |

Rules:

- Shop pages are cached by URL for 30 minutes.
- Idealo/Geizhals aggregate results are cached by URL for 30 minutes.
- A blocked page or parse failure is a source-level warning, not an error.
- One search call per `/deal`, not one per source where avoidable.

### WP 3 — Deduplication and ranking

- Product key: GTIN/EAN if a source exposes it; otherwise normalized title with storage,
  color and condition tokens kept.
- Deduplicate same shop + same condition + same total price.
- Group by condition; compute:
  - cheapest total per condition,
  - cheapest total overall,
  - savings vs. catalog median and historical low where available.
- Do not call an external offer "best" without a known total or an explicit
  "plus shipping" marker.
- Keep the catalog verdict authoritative for catalog facts; external offers are clearly
  labeled.

### WP 4 — `/deal` integration

Replace the current ad-hoc web-find/idealo blocks in `_async_deals` with the engine.

Payload additions:

```text
offers[]        normalized offers
source_status[] which source returned what / failed
best_by_condition
```

Formatting (localized):

```text
iPhone Air — 14 catalog matches · 6 compared

Bester Neupreis: 799 € · MediaMarkt
Bester Refurbished: 669 € · Asgoodasnew
Bester Gebrauchtpreis: 639 € · idealo B-Ware
Bester Gesamtdeal: 669 € (refurbished)

Weitere Quellen: Geizhals ab 799 € · idealo neu ab 884,99 €
```

Keep the existing catalog table as the factual base. Add a compact source/status footer.

`/track N` must select any trackable normalized offer, not only web finds. For 0.8.0 the
actual external tracking can still be a stub that explains that 0.9.0 adds persistence;
do not claim it works before 0.9.0.

### WP 5 — Configuration

Under **AI / Web**:

- `web_deals` (already exists as web search),
- `comparison_portals` (idealo),
- new `geizhals` toggle (default on when web search is on),
- maybe `max_web_shops` (3–6, default 6).

No new account or key.

### WP 6 — Tests and docs

New tests:

- `test_offers.py`: dedupe, product keys, totals, condition grouping, ranking.
- `test_sources.py`: shop/idealo/geizhals parser fixtures; blocked source; timeout;
  cache hit.
- `test_telegram.py`: `/deal` with two sources; partial failure; `/track` selects the
  right offer; no-source fallback.
- Updated format tests for the new blocks.

Acceptance:

- `/deal` still works with all external sources off (catalog only).
- With sources on, at least Pricewatcha + 2 web sources are attempted; a blocked source
  shows a status line, not an exception.
- No duplicate shop/condition entries.
- 0.7.0 behavior is preserved when no external source returns data.
- All existing 162 tests still pass; new tests added.
- README and `docs/phase3.md` updated; version 0.8.0.

## 0.8.0 release criteria

- [x] Offer model and source contract merged.
- [x] Pricewatcha, web shops, idealo and Geizhals adapters.
- [x] Condition-aware `/deal` output.
- [x] `/track` selects any trackable offer (catalog fallback + web; aggregates explain they are not trackable).
- [x] Tests, docs, HACS release.

---

# 0.9.0 — Universal tracker, history and alerts

## Objective

Every normalized offer can be tracked and refreshed, with its own snapshots, history and
alerts — not just Pricewatcha catalog products.

## Work packages

### WP 1 — Persistent offer store

New `custom_components/pricewatcha/tracking.py` using HA `Store`
(`pricewatcha.tracked_offers`, versioned):

```text
TrackedOffer
  id
  source, source_id, url
  product_key, title, shop, condition
  currency
  created_at, last_checked_at
  last_price, last_shipping, last_total
  target_total | None
  notify_on_drop: bool
  active: bool
  snapshots[]  { at, price, shipping, total, availability }
```

- Cap snapshots (e.g. 90 days, one per day plus every change).
- Store per chat ownership and a global "household" view.
- Migration test from empty/old schema.

### WP 2 — Refresh engine

`OfferTracker`:

- refresh every N minutes (config, default 60) for tracked offers,
- source-specific refresh:
  - Pricewatcha product,
  - web shop page JSON-LD,
  - idealo/Geizhals aggregate only if a stable URL/price exists,
- per-source backoff and "blocked" status,
- no refresh storm: jitter, max concurrent, cache.

### WP 3 — Commands

- `/track N` — persist a normalized offer from the last `/deal`.
- `/track <url>` — best-effort create from a URL (source detection).
- `/watchlist` — tracked offers with source, current total, change since start.
- `/untrack N` — stop tracking.
- `/history N` — snapshots and trend for a tracked offer.
- `/alerts` — include external-tracked alerts alongside Pricewatcha alerts.

### WP 4 — Alerts

- target total (`unter 650 €`),
- any drop / percent drop,
- condition filter (new/refurbished/used),
- dedupe: one notification per offer per drop event,
- state persisted so restarts do not re-notify,
- Telegram message with old/new total, shop, source and link.

### WP 5 — AI and natural language

- Context gets tracked offers and snapshots.
- Planner tools:
  - `track_offer(ref)`,
  - `untrack_offer(ref)`,
  - `list_tracked_offers()`,
  - `create_offer_alert(ref, target_total | any_drop)`,
  - `offer_history(ref)`.
- "beobachte das Angebot von MediaMarkt" now creates a real tracked external offer,
  instead of sending the URL to Pricewatcha.
- `/ask` can reason about external history.

### WP 6 — Services and automations

New services/tools:

- `pricewatcha.track_offer`,
- `pricewatcha.untrack_offer`,
- `pricewatcha.list_tracked_offers`,
- `pricewatcha.offer_history`,
- `pricewatcha.create_offer_alert`,
- `pricewatcha.delete_offer_alert`.

Keep existing `watch`/`unwatch` for catalog products; document the difference.

### WP 7 — Tests and docs

- store persistence/restart, snapshot capping, migration,
- refresh success/blocked/timeout,
- drop detection and dedupe,
- commands and natural-language tool calls,
- no duplicate notifications across restarts,
- docs, README, version 0.9.0.

## 0.9.0 release criteria

- [x] A web offer survives a restart and is still tracked.
- [x] `/history` shows at least two snapshots.
- [x] A price drop produces exactly one Telegram notification.
- [x] `/untrack` removes it and stops refreshes.
- [x] All tests, docs, HACS release.

---

## Suggested order

1. 0.8.0 first: offers, sources, dedupe, ranking, `/deal` output.
2. 0.9.0 second: store, refresh, history, alerts, AI tools.
3. Optional later: classifieds (Kleinanzeigen/Vinted) as a separate opt-in source.

## Risks

| Risk | Mitigation |
| --- | --- |
| Shop/portals block requests | per-source timeout, cache, status line, no retry storm |
| Markup changes | parser fixtures, graceful empty result, versioned adapters |
| Wrong dedupe | GTIN first; title/variant fallback; never merge different conditions |
| Unknown shipping | label "plus shipping", do not rank as total |
| Store growth | daily buckets, 90-day cap, compact JSON |
| ToS/legal | public pages only, no CAPTCHA bypass, rate limits, user opt-in |
| Tracker misses a drop | regular refresh, snapshot every change, explicit last-checked |

## Ready-to-use goal objectives

0.8.0:

> Implement 0.8.0 free-source deal engine: normalized Offer model, OfferSource adapters
> for Pricewatcha, direct web shops, idealo and Geizhals, deduplication, condition-aware
> ranking, `/deal` integration, `/track` selection, tests and docs. No paid APIs or new
> accounts. All 162 existing tests must stay green.

0.9.0:

> Implement 0.9.0 universal tracker: persistent TrackedOffer store, refresh engine,
> snapshots, `/track`/`/watchlist`/`/untrack`/`/history`, external alerts, AI tools and
> services, tests and docs. Must survive restart and avoid duplicate notifications.
