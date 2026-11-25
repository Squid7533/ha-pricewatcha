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
- **Telegram commands** — `/deal`, `/ask`, `/search`, `/track`, `/watch`, `/alerts`,
  `/alert`, `/reset` from your phone, plus price-drop pushes, with no YAML and no chat
  id to look up. `/track 1` watches a web offer the last `/ask` found.
- **Optional AI assessment** — bring your own OpenAI-compatible endpoint (DeepSeek,
  OpenAI, Ollama, LM Studio, …) or Anthropic, and `/deal` gains a short second opinion
  plus `/ask` follow-ups with current web results and sources. Off by default; the
  deterministic answer never depends on it.
- **Product photo** — `/deal` sends the top offer's photo when the merchant page
  exposes one; a switch under **Configure → General** turns it off.
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
| `pricewatcha.list_alerts` / `.create_alert` / `.update_alert` / `.delete_alert` | Manage Pricewatcha price alerts |
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
| `/deal iphone 18 pro` | best value and a condition-aware best-offer block across catalog, web shops, idealo and Geizhals |
| `/ask is 669 a good price?` | ask the configured AI about your last `/deal` |
| anything without a slash | an action goes to the AI planner; a question becomes `/ask`; otherwise a search |
| `/search iphone 18 pro` | numbered catalog matches with prices and shops |
| `/track <url>` | track a URL and watch it |
| `/track 1` | watch the first web offer from the last `/ask`; `/track` alone lists them |
| `/watch 1` | watch the first result from the last `/deal` or `/search` |
| `/unwatch 1` | stop watching the first entry from `/watchlist` |
| `/watchlist` | watched products with prices and numbers |
| `/history 1` | price history of a result or watched product |
| `/product 1` | details of a result or watched product |
| `/alerts` | your price alerts |
| `/alert 1324 min 620` | change an alert: `min`/`max` (or `off`), `drop`/`rise` `on/off`, `drop only` (any drop, no threshold), `pause`, `resume`, `name …`; several fields in one line |
| `/alert new 1 min 620 name …` | create an alert for the first result |
| `/alert show 1324` / `/alert delete 1324` | show or delete an alert |
| `/status` | watchlist size, last poll, AI spend, API reachability |
| `/refresh` | poll now |
| `/reset` | forget the current conversation |
| `/help` | the command list |

`/alert` is the bot-side manager: `/alert new 1 min 620` creates an alert for the
first result, `/alert 1324 …` changes only the field you name, and `/alert show` /
`/alert delete` handle the rest. The ids come from `/alerts`. `/alert 1324 drop only`
turns it into "notify on any drop" and clears min/max; `/alert 1324 drop on min off`
does the same in two explicit fields. Natural language „ändere den Preisalarm auf
preis fallend ohne Wert“ maps to that as well.

When `/ask` used the web, its sources are numbered under the answer and stored with the
conversation. `/track 1` watches the first one without pasting a URL; `/track` without an
argument lists the current sources. This works after a restart too, for as long as the
conversation is kept.

The last `/deal` (payload, AI assessment and the last few questions) is remembered
**per chat for 3–7 days** (default 7, `Conversation memory (days)` under General), across
Home Assistant restarts: `/ask` works without a new search, and with AI configured a
plain question after a deal is treated as a follow-up. `/reset` forgets it; a new
`/deal` starts a fresh conversation. The memory lives in
`config/.storage/pricewatcha.conversations`.

### Free-source offer engine

0.8.0 normalizes every result into one `Offer`: Pricewatcha catalog deals, direct shop
pages, idealo and Geizhals. `/deal` adds a compact block before the catalog table:

```
Beste Angebote
Bester Neupreis: 799 € · MediaMarkt · zzgl. Versand
Bester Refurbished-Preis: 669 € · Asgoodasnew · zzgl. Versand
Bester Gebrauchtpreis: 639 € · idealo · zzgl. Versand
```

- Conditions are never mixed: new, refurbished, used and unknown are separate.
- A known total (`price + shipping`) wins; unknown shipping is labeled, never treated as
  zero.
- Offers are deduplicated by product key, shop and condition.
- Geizhals is a new free source, on by default under **Configure → AI → Include Geizhals
  comparison**; the idealo comparison remains opt-in.
- `/track 1` selects the first web offer; if there is none, the same number selects the
  first normalized catalog offer and watches it. Aggregate comparison prices explain
  that they are not trackable.

The old **Web finds** and **Preisvergleich** sections remain as the detailed external
view. A failed source only removes its rows; the catalog answer still stands.

### Web finds in `/deal`

After the catalog table, `/deal` searches the open web for the same product once. It
fetches the first three direct shop results (not comparison sites) and reads the
Schema.org Product offer, so the price is the shop's price, not a snippet guess. Up to
three offers within 135% of the catalog's best price are shown under **Web finds
(unverified)**, including condition when the page states it. They are clearly separate
from the catalog and never change the catalog verdict; if one is cheaper, the verdict
adds a note. `/track 1` watches the first web find. Turn the search off with
**Configure → AI → Use web search for /ask and /deal**.

### Idealo comparison (optional)

idealo has no public API for competitor offers — its official APIs are merchant-side
(PWS 2.0 for uploading your own offers and the order API). As an optional extra,
**Configure → AI → Include idealo comparison** reads the public JSON-LD on the idealo
product page and adds an aggregate block:

```
Preisvergleich (ungeprüft)
idealo: neu ab 884,99 € · 28 Angebote · B-Ware/Gebraucht ab 639,13 €
```

This is the **lowest aggregate price**, not a shop: shipping is excluded, the used price
is idealo's B-Ware/Gebraucht bucket, and the idealo page cannot be `/track`ed. It is
therefore its own unverified block and never changes the catalog ranking. The option is
**off by default** because idealo can change its markup or block automated requests; a
failed fetch simply omits the block. For reliable per-shop offers, a paid price API
(such as PricePirate) would be the alternative.

### Talking to the bot

With an AI provider that supports tool calls (DeepSeek, OpenAI and most
OpenAI-compatible endpoints), free text that looks like an action is planned by the
model, not matched by a regex:

- „beobachte das Angebot von MediaMarkt“
- „ändere den Preisalarm auf 400 Euro“
- „zeig meine Watchlist“
- „lösche Alarm 1324“
- „wie war der Preisverlauf?“

The planner sees the last catalog results (`1`, `2`, …), the web offers from `/ask`
(`w1`, …), the watchlist (`l1`, …) and the alerts (`a1324`), and calls exactly one of the
bot actions. The bot then performs it and answers with the same formatting as the
command. Providers without tool calls get a hint to use `/watch`, `/alert`, `/history`
and friends instead.

A `/deal` reply looks like this (bold prices and links render in Telegram). When the
shop exposes a product image, a photo follows the table — switch it off under
**Configure → General → Send a product photo with /deal**.

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

Verdict: 669 € (very good) at Asgoodasnew, 4.3% below its own average, the lowest price
recorded for this variant. New costs 220 € more, like new 30 € more. Like new is usually
the best compromise.
```

The condition is read from the product name or, where the merchant only encodes it
there, from the URL slug; an offer with no stated condition is shown as `unrated`
instead of being guessed as new.

The language is set under **Configure → General** and applies to the whole reply — the
table, the verdict, help and the AI paragraph. English is the default; German is a
dropdown choice.

### AI assessment (optional)

The advisor is presentation only: it receives the same structured deal data the table
is built from and writes the verdict. With AI enabled the table leaves the deterministic
`Verdict` line out, so there is one judgment instead of two; if the model fails, times
out, runs out of budget or invents a price that is not in the data, the deterministic
verdict is sent as its own short message instead.

Configure it under **Settings → Devices & services → Pricewatcha → Configure → AI
assistant**:

| Setting | Notes |
| --- | --- |
| Provider | `OpenAI-compatible` covers DeepSeek, OpenAI, Ollama, LM Studio, vLLM, OpenRouter, Groq; `Anthropic` uses the messages API |
| Base URL | DeepSeek `https://api.deepseek.com`, OpenAI `https://api.openai.com/v1`, Anthropic `https://api.anthropic.com`, Ollama `http://192.168.1.10:11434/v1` |
| Model | Free text. DeepSeek preset is `deepseek-flash`; Ollama e.g. `llama3.1` |
| API key | Your own key; leave empty for a local server |
| Web search | DeepSeek uses its native search for `/ask`; every other provider uses DuckDuckGo (no key), or SearXNG if configured. `/deal` uses the same DDG/SearXNG path for confirmed shop offers |
| Language | Set under **General**; applies to the table and the AI text |
| Budgets | USD per day and per month, checked before every call; `0` means no limit |

DeepSeek is the default preset because it is cheap and OpenAI-compatible. For
`deepseek-flash` the integration disables thinking mode (a short assessment does not
need it) and reports the spend in the footer of each AI message, e.g.
`deepseek-flash · 0,0006 $ heute`. A typical answer costs a fraction of a cent.

For a self-hosted model, remember that `localhost` inside the Home Assistant container
is not your workstation: use the host's LAN address, or the add-on's internal URL when
Ollama runs as a Home Assistant add-on. Local servers usually need no key.

DuckDuckGo search and the product-photo page fetch need browser TLS impersonation; the
integration therefore declares `curl_cffi`, which Home Assistant installs with the
update. SearXNG uses plain HTTP and needs no extra package.

`/ask` is where future questions belong: the model sees the dated price history and may
give a reasoned estimate about Black Friday or a next price step, clearly labelled as an
estimate. Web search is on by default: DeepSeek uses its server-side search, every other
provider gets the top DuckDuckGo results (or SearXNG, if a URL is configured) before the
model call. The sources are appended below the answer as links. `/deal`'s assessment is
stricter and only uses catalog prices, so the table stays a fact sheet.

Privacy: only the product name, prices, shops, conditions and your `/ask` question are
sent to the provider you chose — no Home Assistant states, entities or location. With
web search on, the question also goes to DeepSeek, DuckDuckGo or your SearXNG instance.
For the product photo, Home Assistant fetches the merchant page and uploads the image to
Telegram. The chat memory (last deal, assessment, questions) is stored in
`config/.storage/pricewatcha.conversations` for 3–7 days (default 7). The key is stored in Home
Assistant's `.storage` in plain text, as with other integration settings; a local model
avoids the model provider, but not the search query.

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

- **Assist intents** — the AI advisor is Telegram-only for now; a conversation agent
  that calls `best_deal` directly is not wired up yet.
- **Complete external coverage** — `/deal` now confirms prices on the first two direct
  shop results and lists them as unverified web finds; comparison sites (idealo,
  billiger, geizhals) are skipped, and a blocked shop page means no web find. It is not a
  full market scan.
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
