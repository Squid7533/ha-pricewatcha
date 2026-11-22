# Phase 3 — the optional AI layer

This plan keeps the integration useful without an AI key and adds one only as an
optional layer on top of the deterministic answer. 3a is implemented through 0.4.2;
3b–3d are deliberately paused until the next review.

## Principles

1. **Pricewatcha stays the source of facts.** Catalog, prices, history, watchlist and
   alerts come from the API. The AI never supplies a price; the deterministic table is
   always sent.
2. **Bring your own provider.** Any OpenAI-compatible endpoint (DeepSeek, OpenAI,
   Ollama, LM Studio, vLLM, OpenRouter, Groq, …) plus Anthropic. The user sets base
   URL, model and key; self-hosted servers work with an empty key.
3. **Off by default.** Existing installations behave exactly as before.
4. **Fail closed.** Timeout, auth error, exhausted budget or an invented price drops
   the AI paragraph and leaves the deterministic reply untouched.
5. **No new mandatory dependencies.** `aiohttp` is already provided by Home Assistant.

## 3a — advisor (done in 0.4.0)

- Options menu: **General** and **AI assistant**.
- Settings: enabled, provider, base URL, model, key, language (de/en, under General),
  max tokens, temperature, timeout, input/output cost per 1M tokens, daily/monthly
  budget.
- DeepSeek defaults: `https://api.deepseek.com`, `deepseek-flash`, thinking disabled.
- `/deal` sends the table first and the AI assessment as a second message; with AI on,
  the table omits the deterministic verdict so there is one judgment, not two. If the
  model fails, the deterministic verdict follows as a short message.
- `/ask <question>` answers about the last `/deal` in that chat and receives the
  previous assessment so the two answers stay consistent. It may reason about future
  sales from the dated history, and with DeepSeek it can use the server-side web search
  with citations.
- Grounding: compact JSON of the deal payload; a price the model invents (≥ 40 €, not a
  known value or a difference of two known values) rejects the whole answer.
- Budget: worst-case estimate checked before the call, actual cost recorded afterwards
  in `pricewatcha.ai_usage`; the current spend is shown in the AI footer.
- Tests: client parsing (OpenAI + Anthropic), error mapping, grounding, budget, routing.

**Acceptance:** without AI the 0.3.4 output is unchanged; with a DeepSeek key `/deal`
gets an assessment and `/ask` answers; a wrong key, a timeout or a spent budget never
breaks the deterministic reply; 138 tests pass.

## 3b — provider-neutral web search for `/ask` (done in 0.4.3)

Goal: **every** configured model — including Ollama, LM Studio, vLLM, OpenRouter and
Groq — can look up current offers, sales and news for a question and cite what it used.
`/deal` stays catalog-only; external offers are never mixed into its table.

### Decision (2026-11-22)

There is **no SearXNG instance** available for this installation. DuckDuckGo therefore
became the default external search; SearXNG stays an optional base URL and nothing in
the design assumes it exists. DuckDuckGo's HTML endpoint rejects plain `aiohttp` with a
202 challenge, so the fetch uses `curl_cffi` (browser TLS impersonation, the same tool
`price_watch` uses); it is declared in the manifest and installed by HA.

### Order of preference

1. **Native provider tool**, when the provider has one and it is enabled:
   - DeepSeek `deepseek_web_search` — already implemented in 0.4.2 (Chat Completions,
     thinking disabled, citations from `message.annotations`).
   - Anthropic `web_search` and OpenAI `web_search` can be added later; each has its own
     request/response shape and pricing.
2. **SearXNG JSON**, when `searxng_url` is configured — preferred for privacy and
   reliability, but optional.
3. **DuckDuckGo HTML** (`https://html.duckduckgo.com/html/?q=…`) as the zero-config
   fallback: free, no key, no setup. Brittle (markup changes, occasional rate limits),
   which is why SearXNG remains an option.
4. **No search**: answer from the price history and general market knowledge when the
   search fails or the user turned it off. A failed search must never become an error.

### Data flow

1. Build a query from the product/query plus the question, dropping filler words and
   capping the length (e.g. `iPhone Air wolkenweiß Black Friday Angebote 2026`).
2. Fetch the top ~5 results: title, URL, snippet. 0.4.3 deliberately stops at snippets;
   fetching the top pages for JSON-LD price/image (the `price_watch` pattern) is a later
   refinement.
3. Hand the results to the model as compact JSON. The system prompt:
   - may only cite URLs from that JSON — no invented links or shops;
   - separates catalog data (below) from web findings;
   - may give a reasoned estimate about future sales, clearly labelled;
   - mentions external prices only with a source.
4. Append up to four sources below the answer, in the same `Quellen` layout DeepSeek uses.
5. Cache search results per query for ~30 minutes; fetch timeout ~8 s, page timeout ~5 s.
   On failure, continue without web results.

### Configuration

| Key | Meaning |
| --- | --- |
| `ai_web_search` | existing on/off switch for `/ask` |
| `web_search_provider` | `auto` (default), `duckduckgo`, `searxng` |
| `searxng_url` | optional self-hosted instance, used when provider is `auto` or `searxng` |

No key is needed for DuckDuckGo or SearXNG. Brave/Tavily can be added later behind the
same interface if a user wants more reliability and is willing to bring a key.

### Acceptance (met in 0.4.3)

- Ollama + DuckDuckGo answers a Black Friday question with current offers and sources.
- DuckDuckGo/SearXNG unreachable → catalog-only answer, no user-facing error and no
  broken Telegram message.
- `/deal` output is unchanged; external prices still cannot enter its assessment.
- Only URLs returned by the search provider appear as sources.
- Tests mock the search HTTP; CI never calls DuckDuckGo or a real SearXNG.
- The DuckDuckGo path was verified live on 2026-11-22: idealo, MediaMarkt, billiger.de
  and CHIP came back for "iPhone Air wolkenweiß Black Friday Angebote 2026"; sponsored
  `/y.js` redirects are filtered out.

### Risks and open questions

- DuckDuckGo HTML parsing is brittle and may be blocked; SearXNG (or a paid API) is the
  escape hatch.
- Privacy: the query leaves Home Assistant for DuckDuckGo or the SearXNG operator. The
  toggle must stay off-by-choice and the README must say so.
- Rate limits and caching: one search per `/ask`, cached per query.
- Whether to fetch pages at all in the first version, or start with snippets only.
- Shipping/region filtering for external offers is **not** part of 3b; that would be a
  later refinement.
- External offers are **not** merged into `/deal`; that remains a separate decision.

## 3c — photos and condition (done in 0.4.4)

The catalog has no image field, so the top deal's merchant page is fetched once: JSON-LD
`Product` first, Open Graph/Twitter image as fallback, `itemCondition` when the catalog
did not state one. The image is downloaded with `curl_cffi`, written to
`config/pricewatcha_tmp` (the only place `telegram_bot.send_photo` may read from), sent
with the caption "price · shop · condition" and the product name, then deleted.

- Only the **top deal** is fetched, not every merchant page; results are cached for six
  hours and failures for ten minutes.
- A blocked shop or a missing image only costs the photo; the text reply is unaffected.
- `send_photo` is called through Home Assistant's Telegram service, so no direct Bot API
  token handling is needed.
- The toggle lives under **Configure → General → Send a product photo with /deal**.
- The page condition is used only when the catalog has none; it never overrides a stated
  catalog condition.

**Acceptance:** a real asgoodasnew page yields a 14 KB JPEG and `refurbished`; the photo
arrives after the table; disabling the toggle sends text only; a missing image or a
blocked shop sends no photo and no error; 138 tests pass.

## 3d — conversation memory (done in 0.4.5)

The last deal, the AI assessment and the last four question/answer pairs are stored per
chat in `config/.storage/pricewatcha.conversations` (Store, version 1). The TTL is
configurable under General between **3 and 7 days** (default 7) and is applied on load,
on save and before every use; `/reset` clears the chat, and a new `/deal` starts a fresh
conversation.

- `/ask` works after a Home Assistant restart without a new search.
- Free text after a deal becomes a follow-up when it looks like a question (`?`, a
  question word such as "ist/soll/kann/wird/lohnt/was", or an exact "black friday"),
  otherwise it remains a product search.
- The question history (last 4, 600 chars each) is passed to the model as context; the
  previous assessment is still passed separately.
- The store only contains the catalog payload, the assessment and the Q&A text; no API
  keys or Home Assistant state.

**Acceptance:** a restarted frontend answers `/ask` from the stored deal; `/reset` clears
it and `/ask` asks for a new `/deal`; entries older than the configured 3–7 days are
ignored; free text "ist 669 gut?" is a follow-up while "iphone 15 pro" searches;
138 tests pass.

## Release plan

| Phase | Version | Content |
| --- | --- | --- |
| 3a | 0.4.0 | AI advisor, provider config, `/ask`, budgets |
| 3a.1 | 0.4.1 | one verdict (AI replaces the deterministic one), full-language replies |
| 3a.2 | 0.4.2 | `/ask` may reason about future sales; DeepSeek web search with sources |
| 3b | 0.4.3 | provider-neutral `/ask` search: DuckDuckGo default, optional SearXNG |
| 3c | 0.4.4 | product photos / condition |
| 3d | 0.4.5 | persistent conversation memory (`/reset`) |
| 3d.1 | 0.4.6 | conversation TTL configurable 3–7 days, default 7 |
