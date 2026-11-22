"""Constants for the Pricewatcha integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "pricewatcha"
NAME: Final = "Pricewatcha"
VERSION: Final = "0.1.0"

API_BASE: Final = "https://pricewatcha.com/api/v1"
API_TIMEOUT: Final = 30
API_TRACK_TIMEOUT: Final = 40  # the track endpoint long-polls for ~25s

CONF_API_KEY: Final = "api_key"
CONF_SCAN_INTERVAL: Final = "scan_interval"

DEFAULT_SCAN_INTERVAL: Final = 30  # minutes
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
