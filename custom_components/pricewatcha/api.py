"""Async client for the Pricewatcha API v1.

Only aiohttp is used, which Home Assistant already provides, so the integration has
no extra requirements. Every call raises a typed error: the API's structured
``error.code`` is carried on the exception rather than being parsed out of messages.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from .const import API_BASE, API_TIMEOUT, API_TRACK_TIMEOUT


class PricewatchaError(Exception):
    """Base error for the Pricewatcha API."""


class PricewatchaAuthError(PricewatchaError):
    """The API key is missing, revoked or wrong."""


class PricewatchaRateLimitError(PricewatchaError):
    """A quota was exceeded; ``retry_after`` is the server's hint in seconds."""

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class PricewatchaApiError(PricewatchaError):
    """The API answered with a structured error."""

    def __init__(self, code: str, message: str, status: int | None = None) -> None:
        super().__init__(f"{code}: {message}" if code else message)
        self.code = code
        self.status = status


class PricewatchaClient:
    """Thin wrapper over the public API."""

    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        self._session = session
        self._api_key = api_key

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        timeout: int = API_TIMEOUT,
    ) -> Any:
        url = f"{API_BASE}{path}"
        try:
            async with asyncio.timeout(timeout):
                response = await self._session.request(
                    method, url, params=params, json=json, headers=self._headers
                )
                status = response.status
                retry_after = response.headers.get("X-RateLimit-Reset")
                try:
                    payload = await response.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    payload = None
        except TimeoutError as err:
            raise PricewatchaApiError("timeout", f"{method} {path} timed out", None) from err
        except aiohttp.ClientError as err:
            raise PricewatchaApiError("cannot_connect", str(err), None) from err

        if status == 401:
            raise PricewatchaAuthError("invalid or revoked API key")
        if status == 403 and isinstance(payload, dict):
            code = (payload.get("error") or {}).get("code", "")
            if code == "access_restricted":
                raise PricewatchaApiError(code, "client is blocked by Pricewatcha", status)
            raise PricewatchaApiError(code or "forbidden", "API key lacks access", status)
        if status == 429:
            hint = None
            if isinstance(payload, dict):
                hint = (payload.get("error") or {}).get("retry_after_seconds")
            raise PricewatchaRateLimitError(
                "rate limited", int(hint) if hint else (int(retry_after) if retry_after else None)
            )

        if status >= 400:
            error = (payload or {}).get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict):
                raise PricewatchaApiError(
                    str(error.get("code") or ""), str(error.get("message") or ""), status
                )
            raise PricewatchaApiError("http_error", f"HTTP {status}", status)

        return payload

    # --- reads -----------------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        """Health + discovery; works without a key."""
        return await self._request("GET", "/health")

    async def search(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Keyword search (token AND, minus-prefixed excludes supported)."""
        payload = await self._request("GET", "/search", params={"q": query, "limit": limit})
        return payload if isinstance(payload, list) else []

    async def product(self, product_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/products/{product_id}")

    async def price_history(self, product_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/products/{product_id}/price-history")

    async def track(
        self, url: str, *, watch: bool = False, refresh: bool = False
    ) -> dict[str, Any]:
        """Submit a product URL. Fast shops answer ``completed`` within the long-poll."""
        body: dict[str, Any] = {"url": url}
        if watch:
            body["watch"] = True
        if refresh:
            body["refresh"] = True
        return await self._request("POST", "/track", json=body, timeout=API_TRACK_TIMEOUT)

    async def job(self, job_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/jobs/{job_id}")

    # --- watchlist -------------------------------------------------------------

    async def watchlist(self, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        payload = await self._request("GET", "/watchlist", params={"limit": limit, "offset": offset})
        items = payload.get("items") if isinstance(payload, dict) else None
        return items if isinstance(items, list) else []

    async def watch(self, product_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/products/{product_id}/watch")

    async def unwatch(self, product_id: str) -> dict[str, Any]:
        return await self._request("DELETE", f"/products/{product_id}/watch")

    async def watch_status(self, product_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/products/{product_id}/watch")

    # --- alerts ----------------------------------------------------------------

    async def alerts(self, product_id: str | None = None) -> list[dict[str, Any]]:
        params = {"product_id": product_id} if product_id else None
        payload = await self._request("GET", "/alerts", params=params)
        if isinstance(payload, dict):
            items = payload.get("alerts")
        else:
            items = payload
        return items if isinstance(items, list) else []

    async def create_alert(self, **kwargs: Any) -> dict[str, Any]:
        return await self._request("POST", "/alerts", json=kwargs)

    async def update_alert(self, alert_id: int, **kwargs: Any) -> dict[str, Any]:
        return await self._request("PATCH", f"/alerts/{alert_id}", json=kwargs)

    async def delete_alert(self, alert_id: int) -> None:
        await self._request("DELETE", f"/alerts/{alert_id}")
