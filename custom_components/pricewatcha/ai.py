"""Optional AI advisor for the Telegram front-end.

The advisor is presentation, not data. It receives the deterministic ``best_deal``
payload and writes a short assessment for the chat; the formatted message keeps the
catalog table, so the model cannot become the source of a price. If the model fails,
runs out of budget or invents a price that is not in the payload, the AI paragraph is
dropped and the deterministic answer stands.

One client speaks both OpenAI-compatible chat completions (DeepSeek, OpenAI, Ollama,
LM Studio, vLLM, OpenRouter, Groq, ...) and Anthropic's messages API. Users bring
their own base URL, model and key; nothing here assumes a particular vendor.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    AI_PROVIDER_ANTHROPIC,
    AI_PROVIDERS,
    AI_USAGE_STORAGE_KEY,
    AI_USAGE_STORAGE_VERSION,
    CONF_AI_API_KEY,
    CONF_AI_BASE_URL,
    CONF_AI_DAILY_BUDGET,
    CONF_AI_ENABLED,
    CONF_AI_INPUT_COST,
    CONF_AI_MAX_TOKENS,
    CONF_AI_MODEL,
    CONF_AI_MONTHLY_BUDGET,
    CONF_AI_OUTPUT_COST,
    CONF_AI_PROVIDER,
    CONF_AI_TEMPERATURE,
    CONF_AI_TIMEOUT,
    DEFAULT_AI_BASE_URL,
    DEFAULT_AI_DAILY_BUDGET,
    DEFAULT_AI_ENABLED,
    DEFAULT_AI_INPUT_COST,
    DEFAULT_AI_LANGUAGE,
    DEFAULT_AI_MAX_TOKENS,
    DEFAULT_AI_MODEL,
    DEFAULT_AI_MONTHLY_BUDGET,
    DEFAULT_AI_OUTPUT_COST,
    DEFAULT_AI_PROVIDER,
    DEFAULT_AI_TEMPERATURE,
    DEFAULT_AI_TIMEOUT,
    language_of,
)

_LOGGER = logging.getLogger(__name__)

ANTHROPIC_VERSION = "2023-06-01"
MAX_AI_TEXT = 1200
# Only numbers at or above this look like a price worth validating. Small numbers
# are usually counts, percentages or "30 € more", which the prompt allows.
_PRICE_FLOOR = 40.0
_PRICE_TOLERANCE = 1.5
_PRICE_RE = re.compile(
    r"(\d[\d., ]*\d|\d)\s*(?:€|eur\b|euro\b|\$|usd\b)"
    r"|(?:€|\$)\s*(\d[\d., ]*\d|\d)",
    re.IGNORECASE,
)


class AIError(Exception):
    """The AI request could not be completed."""


class AIAuthError(AIError):
    """The provider rejected the key."""


class AIRateLimitError(AIError):
    """The provider is rate limiting us."""


class AIResponseError(AIError):
    """The provider answered, but not with usable content."""


@dataclass(slots=True)
class AIConfig:
    """The AI settings as stored in the config entry options."""

    enabled: bool = DEFAULT_AI_ENABLED
    provider: str = DEFAULT_AI_PROVIDER
    base_url: str = DEFAULT_AI_BASE_URL
    model: str = DEFAULT_AI_MODEL
    api_key: str = ""
    language: str = DEFAULT_AI_LANGUAGE
    max_tokens: int = DEFAULT_AI_MAX_TOKENS
    temperature: float = DEFAULT_AI_TEMPERATURE
    timeout: int = DEFAULT_AI_TIMEOUT
    input_cost_per_mtok: float = DEFAULT_AI_INPUT_COST
    output_cost_per_mtok: float = DEFAULT_AI_OUTPUT_COST
    daily_budget_usd: float = DEFAULT_AI_DAILY_BUDGET
    monthly_budget_usd: float = DEFAULT_AI_MONTHLY_BUDGET

    @classmethod
    def from_options(cls, options: dict[str, Any] | None) -> AIConfig:
        options = options or {}
        provider = str(options.get(CONF_AI_PROVIDER, DEFAULT_AI_PROVIDER)).strip().lower()
        return cls(
            enabled=bool(options.get(CONF_AI_ENABLED, DEFAULT_AI_ENABLED)),
            provider=provider if provider in AI_PROVIDERS else DEFAULT_AI_PROVIDER,
            base_url=str(options.get(CONF_AI_BASE_URL, DEFAULT_AI_BASE_URL)).strip(),
            model=str(options.get(CONF_AI_MODEL, DEFAULT_AI_MODEL)).strip(),
            api_key=str(options.get(CONF_AI_API_KEY, "") or "").strip(),
            language=language_of(options),
            max_tokens=_as_int(options.get(CONF_AI_MAX_TOKENS), DEFAULT_AI_MAX_TOKENS),
            temperature=_as_float(
                options.get(CONF_AI_TEMPERATURE), DEFAULT_AI_TEMPERATURE
            ),
            timeout=_as_int(options.get(CONF_AI_TIMEOUT), DEFAULT_AI_TIMEOUT),
            input_cost_per_mtok=_as_float(
                options.get(CONF_AI_INPUT_COST), DEFAULT_AI_INPUT_COST
            ),
            output_cost_per_mtok=_as_float(
                options.get(CONF_AI_OUTPUT_COST), DEFAULT_AI_OUTPUT_COST
            ),
            daily_budget_usd=_as_float(
                options.get(CONF_AI_DAILY_BUDGET), DEFAULT_AI_DAILY_BUDGET
            ),
            monthly_budget_usd=_as_float(
                options.get(CONF_AI_MONTHLY_BUDGET), DEFAULT_AI_MONTHLY_BUDGET
            ),
        )

    @property
    def configured(self) -> bool:
        """True when the advisor has everything it needs to run a request."""
        return self.enabled and bool(self.base_url) and bool(self.model)

    @property
    def language_code(self) -> str:
        return self.language if self.language in ("de", "en") else "de"


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(slots=True)
class AIResult:
    """One completed AI request."""

    text: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    spent_today: float = 0.0


def endpoint_for(config: AIConfig) -> str:
    """The chat endpoint for a base URL, accepting both ``/v1`` and bare hosts."""
    base = config.base_url.rstrip("/")
    if config.provider == AI_PROVIDER_ANTHROPIC:
        return f"{base}/messages" if base.endswith("/v1") else f"{base}/v1/messages"
    if "api.openai.com" in base and not base.endswith("/v1"):
        base += "/v1"
    return f"{base}/chat/completions"


class AIClient:
    """One chat completion against the configured provider."""

    def __init__(self, session: aiohttp.ClientSession, config: AIConfig) -> None:
        self._session = session
        self._config = config

    async def async_complete(self, system: str, user: str) -> AIResult:
        config = self._config
        if config.provider == AI_PROVIDER_ANTHROPIC:
            payload = {
                "model": config.model,
                "system": system,
                "messages": [{"role": "user", "content": user}],
                "max_tokens": config.max_tokens,
                "temperature": min(max(config.temperature, 0.0), 1.0),
            }
            headers = {
                "content-type": "application/json",
                "anthropic-version": ANTHROPIC_VERSION,
            }
            if config.api_key:
                headers["x-api-key"] = config.api_key
        else:
            payload = {
                "model": config.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": config.temperature,
                "stream": False,
            }
            # OpenAI's newer models want max_completion_tokens; every other
            # OpenAI-compatible endpoint (DeepSeek, Ollama, ...) uses max_tokens.
            if "api.openai.com" in config.base_url:
                payload["max_completion_tokens"] = config.max_tokens
            else:
                payload["max_tokens"] = config.max_tokens
            # DeepSeek's thinking mode is on by default and would spend the answer
            # budget on reasoning; a short assessment does not need it.
            if (
                "deepseek" in config.base_url.lower()
                or config.model.lower().startswith("deepseek")
            ):
                payload["thinking"] = {"type": "disabled"}
            headers = {"content-type": "application/json"}
            if config.api_key:
                headers["authorization"] = f"Bearer {config.api_key}"

        try:
            async with asyncio.timeout(config.timeout):
                async with self._session.post(
                    endpoint_for(config), json=payload, headers=headers
                ) as response:
                    status = response.status
                    body = await response.text()
        except TimeoutError as err:
            raise AIError(f"request timed out after {config.timeout}s") from err
        except aiohttp.ClientError as err:
            raise AIError(f"cannot connect: {err}") from err

        if status in (401, 403):
            raise AIAuthError("the provider rejected the API key")
        if status == 429:
            raise AIRateLimitError("the provider is rate limiting requests")
        if status >= 400:
            raise AIResponseError(f"HTTP {status}: {body[:200]}")

        try:
            data = json.loads(body)
        except ValueError as err:
            raise AIResponseError("the provider returned invalid JSON") from err

        if config.provider == AI_PROVIDER_ANTHROPIC:
            result = _parse_anthropic(data)
        else:
            result = _parse_openai(data)
        result.cost_usd = (
            result.input_tokens * config.input_cost_per_mtok
            + result.output_tokens * config.output_cost_per_mtok
        ) / 1_000_000
        return result


def _parse_openai(data: dict[str, Any]) -> AIResult:
    try:
        choice = data["choices"][0]
        content = choice.get("message", {}).get("content")
    except (KeyError, IndexError, TypeError) as err:
        raise AIResponseError("the provider returned no message") from err

    if isinstance(content, list):
        text = "".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict)
        )
    else:
        text = str(content or "")
    if not text.strip():
        raise AIResponseError("the provider returned an empty message")

    usage = data.get("usage") or {}
    return AIResult(
        text=text,
        model=str(data.get("model") or ""),
        input_tokens=_as_int(usage.get("prompt_tokens"), 0),
        output_tokens=_as_int(usage.get("completion_tokens"), 0),
    )


def _parse_anthropic(data: dict[str, Any]) -> AIResult:
    blocks = data.get("content")
    if not isinstance(blocks, list):
        raise AIResponseError("the provider returned no content")
    text = "".join(
        str(block.get("text", ""))
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    )
    if not text.strip():
        raise AIResponseError("the provider returned an empty message")

    usage = data.get("usage") or {}
    return AIResult(
        text=text,
        model=str(data.get("model") or ""),
        input_tokens=_as_int(usage.get("input_tokens"), 0),
        output_tokens=_as_int(usage.get("output_tokens"), 0),
    )


class AIUsage:
    """Persist today's and this month's AI spend and enforce the budgets."""

    def __init__(
        self,
        hass: HomeAssistant,
        config: AIConfig,
        store: Store[dict[str, Any]] | None = None,
    ) -> None:
        self._config = config
        self._store = store or Store(
            hass, AI_USAGE_STORAGE_VERSION, AI_USAGE_STORAGE_KEY
        )
        self._state: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    async def _async_state(self) -> dict[str, Any]:
        if self._state is None:
            stored = await self._store.async_load() or {}
            self._state = {
                "day": str(stored.get("day") or ""),
                "spent_today": _as_float(stored.get("spent_today"), 0.0),
                "month": str(stored.get("month") or ""),
                "spent_month": _as_float(stored.get("spent_month"), 0.0),
            }
        today = dt_util.now().date()
        day = today.isoformat()
        month = day[:7]
        if self._state["day"] != day:
            self._state["day"] = day
            self._state["spent_today"] = 0.0
        if self._state["month"] != month:
            self._state["month"] = month
            self._state["spent_month"] = 0.0
        return self._state

    async def async_can_spend(self, estimate: float) -> tuple[bool, str]:
        """Whether one more request fits into both budgets (0 = no limit)."""
        async with self._lock:
            state = await self._async_state()
            if (
                self._config.daily_budget_usd > 0
                and state["spent_today"] + estimate > self._config.daily_budget_usd
            ):
                return False, "daily budget"
            if (
                self._config.monthly_budget_usd > 0
                and state["spent_month"] + estimate > self._config.monthly_budget_usd
            ):
                return False, "monthly budget"
            return True, ""

    async def async_record(self, cost: float) -> float:
        """Add a completed request's cost and return today's total."""
        async with self._lock:
            state = await self._async_state()
            state["spent_today"] = round(state["spent_today"] + cost, 6)
            state["spent_month"] = round(state["spent_month"] + cost, 6)
            await self._store.async_save(dict(state))
            return float(state["spent_today"])


def estimate_cost(config: AIConfig, system: str, user: str) -> float:
    """Worst-case cost of one request, used before spending anything."""
    input_tokens = max(1, (len(system) + len(user)) // 4)
    return (
        input_tokens * config.input_cost_per_mtok
        + config.max_tokens * config.output_cost_per_mtok
    ) / 1_000_000


_SYSTEM_PROMPTS = {
    "de": (
        "Du bist ein nüchterner Preisberater in einem Telegram-Bot. Der Bot zeigt "
        "bereits eine Tabelle mit allen Preisen; deine Einschätzung ist das Fazit.\n"
        "Aufgabe: in zwei bis drei Sätzen (höchstens 80 Wörter) sagen, ob der beste "
        "Deal gut ist und ob sich ein Aufpreis für einen besseren Zustand lohnt. "
        "Konzentriere dich auf die Kapazität des besten Deals; andere Kapazitäten nur, "
        "wenn die Frage sie betrifft.\n"
        "Regeln:\n"
        "- Nutze nur Preise, Shops, Zustände und Zahlen aus den Daten. Erfinde nichts.\n"
        "- Wiederhole nicht die Preistabelle; nenne höchstens zwei Beträge.\n"
        "- Keine Spekulation über künftige Aktionen (z. B. Black Friday) oder Shops, "
        "die nicht in den Daten stehen. Wenn die Daten eine Frage nicht beantworten, "
        "sage das klar.\n"
        "- Keine Tabellen, kein Markdown, keine Aufzählungszeichen, keine Emojis.\n"
        "- Antworte auf Deutsch."
    ),
    "en": (
        "You are a level-headed price adviser in a Telegram bot. The bot already shows "
        "a table with every price; your assessment is the verdict.\n"
        "Job: in two or three sentences (at most 80 words), judge whether the best "
        "deal is good and whether paying more for a better condition is worth it. "
        "Focus on the capacity of the best deal; mention other capacities only when "
        "the question is about them.\n"
        "Rules:\n"
        "- Use only prices, shops, conditions and numbers from the data. Invent nothing.\n"
        "- Do not repeat the price table; mention at most two amounts.\n"
        "- Do not speculate about future sales (e.g. Black Friday) or shops that are "
        "not in the data. If the data cannot answer a question, say so clearly.\n"
        "- No tables, no markdown, no bullet points, no emojis.\n"
        "- Answer in English."
    ),
}


def _numbers(value: Any, out: set[float]) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        out.add(round(float(value), 2))
    elif isinstance(value, str):
        try:
            out.add(round(float(value), 2))
        except ValueError:
            return
    elif isinstance(value, dict):
        for item in value.values():
            _numbers(item, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _numbers(item, out)


def _parse_amount(raw: str) -> float | None:
    """Read ``669``, ``1.019``, ``1,019`` and ``669,50`` the way a human means them."""
    text = raw.replace(" ", "").replace("\u00a0", "").strip().rstrip(".,")
    if not text:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        left, _, right = text.partition(",")
        text = left + right if len(right) == 3 and len(left) <= 3 else f"{left}.{right}"
    elif text.count(".") == 1:
        left, _, right = text.partition(".")
        if len(right) == 3 and len(left) <= 3:
            text = left + right
    try:
        return float(text)
    except ValueError:
        return None


def unknown_price(text: str, payload: dict[str, Any]) -> float | None:
    """Return the first price the model invented, or ``None`` when it stayed grounded.

    The deterministic table is always sent, so a hallucinated amount is not fatal —
    but it would undermine the assessment, and the safest response is to drop it.
    """
    allowed: set[float] = set()
    _numbers(payload, allowed)
    prices = [
        float(deal["price"])
        for deal in payload.get("deals") or []
        if isinstance(deal, dict) and deal.get("price") is not None
    ]
    for group in payload.get("groups") or []:
        if not isinstance(group, dict):
            continue
        for offer in group.get("offers") or []:
            if isinstance(offer, dict) and offer.get("price") is not None:
                prices.append(float(offer["price"]))
    # A premium ("30 € more") is a difference of two real prices.
    for left in prices:
        for right in prices:
            allowed.add(round(abs(left - right), 2))

    for match in _PRICE_RE.finditer(text):
        amount = _parse_amount(match.group(1) or match.group(2) or "")
        if amount is None or amount < _PRICE_FLOOR:
            continue
        if not any(abs(amount - value) <= _PRICE_TOLERANCE for value in allowed):
            return amount
    return None


def _clean_text(text: str) -> str:
    cleaned = text.strip().replace("**", "").replace("__", "").replace("`", "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    if len(cleaned) > MAX_AI_TEXT:
        cut = cleaned.rfind(" ", 0, MAX_AI_TEXT)
        cleaned = cleaned[: cut if cut > 0 else MAX_AI_TEXT].rstrip() + "…"
    return cleaned


class AIAdvisor:
    """Build the prompt, enforce the budget and validate what comes back."""

    def __init__(
        self,
        hass: HomeAssistant,
        config: AIConfig,
        *,
        client: AIClient | None = None,
        usage: AIUsage | None = None,
    ) -> None:
        self.config = config
        self.language = config.language_code
        self.last_error: str | None = None
        self._client = client or AIClient(async_get_clientsession(hass), config)
        self._usage = usage or AIUsage(hass, config)
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return self.config.configured

    async def async_advise(
        self,
        payload: dict[str, Any],
        question: str | None = None,
        previous: str | None = None,
    ) -> AIResult | None:
        """Return a grounded assessment, or ``None`` with ``last_error`` set."""
        self.last_error = None
        if not self.available:
            self.last_error = "not_configured"
            return None

        system = _SYSTEM_PROMPTS[self.language]
        user = self._user_prompt(payload, question, previous)
        estimate = estimate_cost(self.config, system, user)

        async with self._lock:
            allowed, reason = await self._usage.async_can_spend(estimate)
            if not allowed:
                self.last_error = (
                    "budget_monthly" if "monthly" in reason else "budget_daily"
                )
                _LOGGER.info("AI request skipped: %s", reason)
                return None

            try:
                result = await self._client.async_complete(system, user)
            except AIAuthError as err:
                self.last_error = "auth"
                _LOGGER.warning("AI authentication failed: %s", err)
                return None
            except AIRateLimitError as err:
                self.last_error = "rate_limit"
                _LOGGER.warning("AI rate limited: %s", err)
                return None
            except AIError as err:
                self.last_error = "timeout" if "timed out" in str(err) else "provider_error"
                _LOGGER.warning("AI request failed: %s", err)
                return None

            text = _clean_text(result.text)
            if not text:
                self.last_error = "empty"
                return None
            invented = unknown_price(text, payload)
            if invented is not None:
                self.last_error = "unknown_price"
                _LOGGER.warning(
                    "AI answer rejected: price %s is not in the deal data", invented
                )
                return None

            result.text = text
            result.spent_today = await self._usage.async_record(result.cost_usd)
            _LOGGER.debug(
                "AI answer from %s: %s tokens in, %s out, $%.6f",
                result.model,
                result.input_tokens,
                result.output_tokens,
                result.cost_usd,
            )
            return result

    def _user_prompt(
        self,
        payload: dict[str, Any],
        question: str | None,
        previous: str | None = None,
    ) -> str:
        compact = _compact_payload(payload)
        data = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
        if self.language == "de":
            tail = (
                f"\n\nFrage des Nutzers: {question}\nAntworte direkt auf die Frage."
                if question
                else "\n\nSchreibe die Einschätzung."
            )
            if previous:
                tail += (
                    "\n\nDeine bisherige Einschätzung (bleibe konsistent, korrigiere "
                    f"sie nur mit gutem Grund): {previous}"
                )
            return f"Daten aus dem Pricewatcha-Katalog:\n{data}{tail}"
        tail = (
            f"\n\nUser question: {question}\nAnswer the question directly."
            if question
            else "\n\nWrite the assessment."
        )
        if previous:
            tail += (
                "\n\nYour previous assessment (stay consistent, correct it only "
                f"with a good reason): {previous}"
            )
        return f"Data from the Pricewatcha catalog:\n{data}{tail}"


def _compact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Only what the model needs, so the prompt stays small and cheap."""
    deals = []
    for deal in (payload.get("deals") or [])[:3]:
        if not isinstance(deal, dict):
            continue
        deals.append(
            {
                key: deal.get(key)
                for key in (
                    "price",
                    "currency",
                    "shop",
                    "condition",
                    "capacity",
                    "vs_average_pct",
                    "average_price",
                    "historical_low",
                    "trend",
                )
                if deal.get(key) is not None
            }
        )
    groups = []
    for group in payload.get("groups") or []:
        if not isinstance(group, dict):
            continue
        offers = [
            {
                key: offer.get(key)
                for key in ("condition", "price", "shop")
                if offer.get(key) is not None
            }
            for offer in (group.get("offers") or [])[:6]
            if isinstance(offer, dict)
        ]
        if offers:
            groups.append({"capacity": group.get("capacity"), "offers": offers})
    compact = {
        "query": payload.get("query"),
        "matched": payload.get("matched"),
        "compared": payload.get("compared"),
        "deals": deals,
        "groups": groups,
    }
    if payload.get("partial"):
        compact["partial"] = {
            "history_missing": bool((payload["partial"] or {}).get("history")),
            "links_missing": bool((payload["partial"] or {}).get("details")),
        }
    return compact
