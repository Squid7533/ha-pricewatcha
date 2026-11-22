"""Runtime data attached to a Pricewatcha config entry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .coordinator import PricewatchaCoordinator
    from .telegram import TelegramFrontend


@dataclass(slots=True)
class PricewatchaRuntime:
    """Everything a loaded entry owns."""

    coordinator: PricewatchaCoordinator
    telegram: TelegramFrontend | None = None
