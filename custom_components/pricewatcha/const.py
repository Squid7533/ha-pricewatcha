"""Constants for the Pricewatcha integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "pricewatcha"
NAME: Final = "Pricewatcha"
VERSION: Final = "0.4.7"

# Fired on the bus whenever a watched product's price changes between polls, so
# notifications (Telegram, mobile push) do not have to enumerate entity ids.
EVENT_PRICE_CHANGED: Final = "pricewatcha_price_changed"

API_BASE: Final = "https://pricewatcha.com/api/v1"
API_TIMEOUT: Final = 30
API_TRACK_TIMEOUT: Final = 40  # the track endpoint long-polls for ~25s

CONF_API_KEY: Final = "api_key"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_TELEGRAM_MIN_DROP: Final = "telegram_min_drop_pct"
CONF_TELEGRAM_PHOTOS: Final = "telegram_photos"
CONF_CONVERSATION_TTL_DAYS: Final = "conversation_ttl_days"

DEFAULT_SCAN_INTERVAL: Final = 30  # minutes
DEFAULT_TELEGRAM_MIN_DROP: Final = 2.0  # percent
DEFAULT_TELEGRAM_PHOTOS: Final = True
DEFAULT_CONVERSATION_TTL_DAYS: Final = 7
MIN_CONVERSATION_TTL_DAYS: Final = 3
MAX_CONVERSATION_TTL_DAYS: Final = 7
MIN_SCAN_INTERVAL: Final = 5
MAX_SCAN_INTERVAL: Final = 1440

PLATFORMS: Final = ["sensor"]

# Names that mark an accessory rather than the product itself. Search is keyword AND
# over name/shop/URL, so "iphone 15 pro" happily matches cases and screen protectors.
ACCESSORY_TERMS: Final = (
    "case",
    "cover",
    "hülle",
    "huelle",
    "glas",
    "glass",
    "displayschutz",
    "panzerglas",
    "screen protector",
    "schutzfolie",
    "wallet",
    "kabel",
    "cable",
    "charger",
    "ladegerät",
    "netzteil",
    "adapter",
    "halterung",
    "mount",
    "skin",
    "folie",
    "bag",
    "sling",
    "tasche",
    "schutz",
    "handykette",
    "armband",
    "laufgürtel",
    "gürtel",
    "belt",
    "strap",
    "kette",
    "keychain",
    "anhänger",
)

# Keyword filtering always leaks something ("Handykette", "Laufgürtel"). A device
# never costs a fraction of what its siblings cost, so anything below this share of
# the median price of the matches is treated as an accessory.
ACCESSORY_PRICE_FLOOR: Final = 0.25

# Sent to the API as minus-prefixed tokens before the local filter even runs. The
# catalog is accessory-heavy: "iphone 15 pro" returned 48 accessories out of 50 rows,
# which left "best deal" comparing two products. Server-side excludes fix the window.
SEARCH_EXCLUDES: Final = (
    "-cover",
    "-case",
    "-hülle",
    "-huelle",
    "-glas",
    "-panzerglas",
    "-displayschutz",
    "-wallet",
    "-kabel",
    "-adapter",
    "-bag",
    "-sling",
)

# --- optional AI advisor -----------------------------------------------------
# The advisor is presentation only: it receives the deterministic best_deal data
# and writes a short assessment. It is off by default; Pricewatcha works without it.
CONF_AI_ENABLED = "ai_enabled"
CONF_AI_PROVIDER = "ai_provider"
CONF_AI_BASE_URL = "ai_base_url"
CONF_AI_MODEL = "ai_model"
CONF_AI_API_KEY = "ai_api_key"
CONF_AI_LANGUAGE = "ai_language"  # legacy key, still read as a fallback
CONF_LANGUAGE = "language"
CONF_AI_MAX_TOKENS = "ai_max_tokens"
CONF_AI_TEMPERATURE = "ai_temperature"
CONF_AI_TIMEOUT = "ai_timeout"
CONF_AI_INPUT_COST = "ai_input_cost_per_mtok"
CONF_AI_OUTPUT_COST = "ai_output_cost_per_mtok"
CONF_AI_DAILY_BUDGET = "ai_daily_budget_usd"
CONF_AI_WEB_SEARCH = "ai_web_search"
CONF_WEB_SEARCH_PROVIDER = "web_search_provider"
CONF_SEARXNG_URL = "searxng_url"
CONF_AI_MONTHLY_BUDGET = "ai_monthly_budget_usd"

AI_PROVIDER_OPENAI = "openai_compatible"
AI_PROVIDER_ANTHROPIC = "anthropic"
AI_PROVIDERS: Final = (AI_PROVIDER_OPENAI, AI_PROVIDER_ANTHROPIC)

DEFAULT_AI_ENABLED: Final = False
DEFAULT_AI_PROVIDER: Final = AI_PROVIDER_OPENAI
# DeepSeek is the default because it is OpenAI-compatible, cheap and the model
# name is stable; every other provider is just a different base_url/model.
DEFAULT_AI_BASE_URL: Final = "https://api.deepseek.com"
DEFAULT_AI_MODEL: Final = "deepseek-flash"
DEFAULT_AI_LANGUAGE: Final = "en"
DEFAULT_LANGUAGE: Final = "en"
DEFAULT_AI_MAX_TOKENS: Final = 700
DEFAULT_AI_TEMPERATURE: Final = 0.3
DEFAULT_AI_TIMEOUT: Final = 30
# deepseek-flash peak input/output, USD per 1M tokens. Peak rates are used as
# the default so a budget estimate never under-spends; off-peak is half. Users of
# other providers adjust these; 0 means "free/unknown" and never triggers a budget.
DEFAULT_AI_INPUT_COST: Final = 0.30
DEFAULT_AI_OUTPUT_COST: Final = 1.20
DEFAULT_AI_DAILY_BUDGET: Final = 1.0
DEFAULT_AI_WEB_SEARCH: Final = True
DEFAULT_WEB_SEARCH_PROVIDER: Final = "auto"
DEFAULT_AI_MONTHLY_BUDGET: Final = 10.0

AI_USAGE_STORAGE_VERSION: Final = 1
AI_USAGE_STORAGE_KEY: Final = f"{DOMAIN}.ai_usage"


def language_of(options: dict[str, object] | None) -> str:
    """The bot language, falling back to the 0.4.0 ``ai_language`` key."""
    options = options or {}
    value = str(
        options.get(CONF_LANGUAGE)
        or options.get(CONF_AI_LANGUAGE)
        or DEFAULT_LANGUAGE
    ).lower()
    return value if value in ("de", "en") else DEFAULT_LANGUAGE


def resolved_search_provider(options: dict[str, object] | None) -> str:
    """Which external search backend should run for this installation.

    Native provider tools (DeepSeek) are handled separately; this only decides the
    fallback used by models that have no web tool of their own.
    """
    options = options or {}
    provider = str(
        options.get(CONF_WEB_SEARCH_PROVIDER) or DEFAULT_WEB_SEARCH_PROVIDER
    ).lower()
    if provider not in ("auto", "duckduckgo", "searxng"):
        provider = DEFAULT_WEB_SEARCH_PROVIDER
    if provider == "searxng" and not options.get(CONF_SEARXNG_URL):
        return "none"
    if provider == "auto":
        return "searxng" if options.get(CONF_SEARXNG_URL) else "duckduckgo"
    return provider


# Persistent per-chat conversation memory (3d)
CONVERSATION_STORAGE_VERSION: Final = 1
CONVERSATION_STORAGE_KEY: Final = f"{DOMAIN}.conversations"
CONVERSATION_TTL_SECONDS: Final = DEFAULT_CONVERSATION_TTL_DAYS * 24 * 3600
CONVERSATION_HISTORY_MAX: Final = 4
CONVERSATION_TEXT_MAX: Final = 600
