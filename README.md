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
  price history, and group the offers by capacity and condition (very good / like new /
  new), so "is it worth paying more?" has a data-backed answer.
- **Services** for search, price history, tracking a URL, watch/unwatch, alerts and an
  immediate refresh, all returning their data so scripts, automations and LLM chats can use
  the answer.
- **Telegram commands** — `/deal`, `/search`, `/track`, `/watch`, `/alerts` from your phone,
  plus price-drop pushes, with no YAML and no chat id to look up.
- **Config flow** — paste the key once in the UI; reauth is supported when you rotate it.

## Install

### HACS (custom repository)

1. HACS → ⋮ → **Custom repositories**
2. URL: `https://github.com/Squid7533/ha-pricewatcha`, category **Integration**
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
| `pricewatcha.best_deal` | Ranked deals with price, condition, history, URL, plus `groups` by capacity and condition |
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
   discount against that product's own average price,
6. looks up each shortlisted product page — the search endpoint returns no URL, and
   some merchants (asgoodasnew) keep the condition ("sehr gut", "wie neu") only in the
   URL slug.

The result is a shortlist with `vs_average_pct`, `vs_low_pct` and the dated
`history` points, plus `groups`: the cheapest offer per capacity and condition. The
Telegram front-end turns that into the "very good 669 € · good 689 € · like new 699 € ·
new 889 €" view and a verdict that names the upcharge for a better condition.

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

## Events

When a watched product's price moves between polls, the integration fires
`pricewatcha_price_changed` on the Home Assistant bus, so notifications don't have to
enumerate entity ids:

| Field | Meaning |
| --- | --- |
| `product_id`, `name`, `shop`, `product_url`, `currency` | which product |
| `old_price`, `new_price` | the change |
| `change`, `change_pct` | absolute and percentage difference |
| `is_drop` | `true` when the price fell |

```yaml
automation:
  - alias: Notify on any drop over 5 %
    triggers:
      - trigger: event
        event_type: pricewatcha_price_changed
    conditions:
      - condition: template
        value_template: "{{ trigger.event.data.is_drop and trigger.event.data.change_pct | abs > 5 }}"
    actions:
      - action: notify.mobile_app_your_phone
        data:
          title: "{{ trigger.event.data.name }} dropped"
          message: >-
            {{ trigger.event.data.new_price }} {{ trigger.event.data.currency }}
            (was {{ trigger.event.data.old_price }})
          data:
            url: "{{ trigger.event.data.product_url }}"
```

## Telegram bot

No YAML, no helpers, no chat id to look up. The integration listens to the events Home
Assistant's Telegram bot already fires and replies through its `send_message` service;
the first chat that writes to the bot becomes the owner and is remembered.

1. **@BotFather** in Telegram → `/newbot` → copy the token.
2. Home Assistant → **Settings → Devices & services → Add integration → Telegram bot** →
   paste the token (polling).
3. Message your bot anything. That's it — it adopts that chat and starts answering.

| Command | Does |
| --- | --- |
| `/deal iphone 18 pro` | best value, condition, price history, links and a verdict |
| anything without a slash | same as `/deal` |
| `/search iphone 18 pro` | raw catalog matches with prices and shops |
| `/track <url>` | track a URL and watch it |
| `/watch <product_id>` | watch a catalog product |
| `/alerts` | your price alerts |
| `/help` | the command list |

A `/deal` reply looks like this (bold prices and links render in Telegram):

```
iphone air wolkenweiß — 14 matches · 10 compared

1. 669 € · Asgoodasnew · very good
Apple iPhone Air 256GB wolkenweiß
4.3% below its own average (699 €) · lowest recorded price · price falling
Price history: 13.10. 739 € → 15.10. 719 € → 03.11. 699 € → 18.11. 669 €
See offer

2. 699 € · Asgoodasnew · like new
...

Prices by condition
256 GB: very good 669 € · good 689 € · like new 699 € · new 889 €
512 GB: very good 769 € · like new 819 € · new 1 019 €

Verdict: Best value: 669 € (very good) at Asgoodasnew, 4.3% below its own average,
the lowest price recorded for this variant. New costs 220 € more, like new 30 € more.
Like new is usually the best compromise.
```

The condition is read from the product name or, where the merchant only encodes it
there, from the URL slug; an offer with no stated condition is shown as `unrated`
instead of being guessed as new.

**Price drops push on their own** once a chat is known: when a watched product falls by at
least the threshold, the bot sends the new price, the old one and the link. The threshold
lives in **Configure** on the integration (default 2 %), and rises are never sent.

Only the first chat is accepted; a second one gets "This bot is already connected to
someone else's Home Assistant." To hand the bot to another chat, delete the
`pricewatcha.telegram` entry in `.storage` and message again.

### Prefer automations?

`examples/telegram.yaml` does the same job as a YAML package. **Do not install both** —
you would get every reply twice. The built-in front-end is the recommended path; the
package exists for people who want the logic visible in their automations.

## Troubleshooting

**The bot ignores me, and the log says `Unauthorized update - ... is in allowed chats: []`.**
Since Home Assistant 2026.2 a Telegram bot drops every message from a chat that is not
registered, before any event is fired — so Pricewatcha never sees the command. Open
**Settings → Devices & services → Telegram bot**, choose **Add allowed chat** and enter your
chat id (message `@userinfobot` in Telegram to get it; the log line names it too). The
integration raises a repair issue when it finds a bot with no allowed chats.

**Commands don't appear in Telegram's `/` menu.** The integration publishes them to the Bot
API at startup, but Telegram caches the menu per chat: close and reopen the chat, or restart
the Telegram app. Typing the commands works regardless.

**Every reply arrives twice.** Both the built-in front-end and `examples/telegram.yaml` are
active — remove the package and restart.

**Telegram replies fail with `Can't parse entities`.** The integration sends
`parse_mode: HTML` for bold prices and tappable links, and escapes every product name,
shop and URL it prints. If Telegram still rejects a message, the integration retries the
same text as `plain_text`, so the reply arrives either way; the debug log shows the
error. Please report the product name that broke it.

**A repair says `target` is deprecated for `telegram_bot.send_message`.** The integration
already picks `chat_id` or `target` from the service schema at runtime, so it keeps working
before and after HA 2026.9. If you also installed `examples/telegram.yaml`, that file now
uses `chat_id` too; older Home Assistants need `target` there instead.

**Updates stop after a Home Assistant restart.** Check the integration is still loaded and
that the allowed chat survived; a re-added bot creates a new entry with no chats.

## Quotas and behaviour

- One watchlist request per poll interval regardless of how many products you watch.
- `best_deal` costs one search, plus one price-history and one product request per
  candidate (about 20 requests for the default 10). The API also enforces an hourly
  read budget; when it runs out, the reply says that links or history are missing
  ("rate limit") instead of presenting a thinner shortlist as complete.
- Authenticated read quota is roughly 3000 requests/day, so a 30-minute interval with
  occasional searches is far inside it.
- Only watched products are refreshed by Pricewatcha's scheduler; a one-shot `track`
  without `watch` does not enrol anything.
- Prices are informational; the merchant page is authoritative.

## Not (yet) included

- **Assist intents** — planned for 0.4.0, so a Claude/OpenAI conversation agent can call
  `best_deal` straight from a chat instead of the `/deal` command.
- Webhook subscriptions (Pricewatcha's push API) — polling covers the current use.

## Development

```bash
pip install homeassistant pytest
python -m pytest tests -q
```

CI runs those tests, `hassfest`, HACS validation, and Home Assistant's own
`check_config` against `examples/telegram.yaml`.


## License

MIT.
