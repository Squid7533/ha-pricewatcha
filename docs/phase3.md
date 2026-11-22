# Phase 3 — the optional AI layer

This plan keeps the integration useful without an AI key and adds one only as an
optional layer on top of the deterministic answer. 3a is implemented in 0.4.0;
3b–3d are deliberately paused until 3a has been reviewed with a real key.

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
breaks the deterministic reply; 97 tests pass.

## 3b — offers outside the catalog (next, after review)

Goal: show Saturn/MediaMarkt/idealo offers that are not in the Pricewatcha catalog,
clearly marked as unverified and never mixed into the catalog table.

Two implementations, chosen by provider:

- **DeepSeek:** server-side `deepseek_web_search` / `deepseek_read_url` tools, with
  citations. Search is free since 2026-11-18, results are billed as tokens.
- **Everything else:** DuckDuckGo/SearXNG candidates → the model may only pick from the
  supplied URLs → JSON-LD/meta backfill for price and image (the `price_watch` pattern).

Open decisions: whether web offers are opt-in per chat, how to mark them, and whether
shipping/region filtering is needed.

## 3c — photos and condition

Fetch the merchant page (JSON-LD/Open Graph) for the top offer, cache the image, and
send it with the Telegram Bot API's `sendPhoto`. HA's `telegram_bot.send_message`
service cannot send photos, so this needs a small direct Bot API call using the bot
token the Telegram entry already stores. Fallback: link preview only.

Open decisions: whether fetching every merchant page is acceptable, and how to handle
shops that block bots.

## 3d — conversation memory

Persist per chat: the last product/query, the last payload and the last few
question/answer pairs, with a TTL and a `/reset`. Free text after a deal can then be a
follow-up instead of a new search.

Open decisions: storage location, retention, and whether free text should become a
follow-up (more magic) or stay a search.

## Release plan

| Phase | Version | Content |
| --- | --- | --- |
| 3a | 0.4.0 | AI advisor, provider config, `/ask`, budgets |
| 3a.1 | 0.4.1 | one verdict (AI replaces the deterministic one), full-language replies |
| 3a.2 | 0.4.2 | `/ask` may reason about future sales; DeepSeek web search with sources |
| 3b | 0.4.3 | web offers for providers without native search (DDG/SearXNG) |
| 3c | 0.4.4 | product photos / condition |
| 3d | 0.4.5 | persistent conversation memory |
