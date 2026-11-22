# Pricewatcha for Home Assistant

Track product prices from [Pricewatcha](https://pricewatcha.com) inside Home Assistant: one
price sensor per watched product, services for search/history/alerts, and a `best_deal`
service that answers "find the best deal for iPhone 18 Pro" with a ranked shortlist rather
than a wall of JSON.

Available through HACS as a custom repository. No API key is needed to read the catalog;
the account features (watchlist, price alerts) need a free
[Pricewatcha API key](https://pricewatcha.com/profile#api-keys).

## What you get

- **One sensor per watched product** — current price (monetary, statistics-friendly) plus
  shop, product URL, status, last checked and watched-at as attributes.
- **`best_deal`** — search, drop accessories, compare each candidate against its *own*
  price history, return the best value first.
- **Services** for search, price history, tracking a URL, watch/unwatch, alerts and an
  immediate refresh, all returning their data so scripts, automations and LLM chats can use
  the answer.
- **Config flow** — paste the key once in the UI; reauth is supported when you rotate it.

## Install

### HACS (custom repository)

1. HACS → ⋮ → **Custom repositories**
2. URL: `https://github.com/samuelengelking/ha-pricewatcha`, category **Integration**
3. Install **Pricewatcha**, then restart Home Assistant.

### Manual

Copy `custom_components/pricewatcha` into your `config/custom_components/` directory and
restart.

## Configure

**Settings → Devices & services → Add integration → Pricewatcha**, paste your API key
(Profile → API keys; the secret is shown only once). The poll interval (default 30 min)
lives in **Configure** on the integration.

## Services

| Service | What it does |
| --- | --- |
| `pricewatcha.search` | Keyword search; returns `results`. Supports minus terms: `iphone 18 pro -cover -case` |
| `pricewatcha.best_deal` | Ranked deals with price, historical low/average, discount %, URL |
| `pricewatcha.price_history` | Low/high/average/trend for one `product_id` |
| `pricewatcha.track` | Submit a product URL; `watch: true` keeps it |
| `pricewatcha.watch` / `.unwatch` | Add/remove a product from the watchlist (adds/removes its sensor) |
| `pricewatcha.list_alerts` / `.create_alert` / `.delete_alert` | Manage Pricewatcha price alerts |
| `pricewatcha.refresh` | Poll now instead of waiting for the interval |

### How `best_deal` ranks

Pricewatcha search is keyword AND over a catalog that is heavy on accessories, so a naive
"cheapest first" answer returns phone cases. `best_deal` therefore:

1. appends minus-terms (`-cover -case -hülle -glas …`) and queries the maximum 200 rows
   (at 50 rows, the same query returned 2 devices instead of 144),
2. drops anything whose name looks like an accessory,
3. drops anything priced below 25 % of the median of the matches — a real device never
   costs a fraction of its siblings,
4. de-duplicates offers listed under several URLs,
5. takes the cheapest `candidates`, fetches each one's price history, and ranks by
   discount against that product's own average price.

The result is a shortlist with `vs_average_pct` and `vs_low_pct`, not a single guess.

```yaml
action: pricewatcha.best_deal
data:
  query: iphone 18 pro
  limit: 3
  max_price: 900
response_variable: deals
```

## Examples

Notify when a watched product drops below a price:

```yaml
automation:
  - alias: iPhone price drop
    triggers:
      - trigger: numeric_state
        entity_id: sensor.iphone_15_pro_128_gb_titan_schwarz
        below: 500
    actions:
      - action: notify.mobile_app_your_phone
        data:
          title: Price drop
          message: >-
            {{ state_attr(trigger.entity_id, 'shop') }}:
            {{ states(trigger.entity_id) }} {{ state_attr(trigger.entity_id, 'currency') }}
          data:
            url: "{{ state_attr(trigger.entity_id, 'product_url') }}"
```

Ask for a deal from a script or an LLM-backed chat:

```yaml
action: pricewatcha.best_deal
data:
  query: "{{ query }}"
  limit: 3
response_variable: deals
```

## Quotas and behaviour

- One watchlist request per poll interval regardless of how many products you watch.
- `best_deal` costs one search plus one price-history request per candidate (default 10).
- Authenticated read quota is roughly 3000 requests/day, so a 30-minute interval with
  occasional searches is far inside it.
- Only watched products are refreshed by Pricewatcha's scheduler; a one-shot `track`
  without `watch` does not enrol anything.
- Prices are informational; the merchant page is authoritative.

## Not (yet) included

- **Assist intents** for LLM tool calling — 0.2.0, so Claude/OpenAI conversation agents can
  call `best_deal` directly in chat.
- Webhook subscriptions (Pricewatcha's push API) — polling covers the current use.

## License

MIT.
