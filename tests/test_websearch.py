"""Tests for the provider-neutral web search used by /ask."""

from __future__ import annotations

import asyncio

import custom_components.pricewatcha.websearch as websearch
from custom_components.pricewatcha.websearch import (
    SearchResult,
    build_query,
    parse_duckduckgo,
    parse_searxng,
    search_web,
)

DDG_HTML = """
<div class="result results_links results_links_deep web-result">
  <a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.giga.de%2Ftech%2F799-iphone&amp;rut=x">
     799 <b>Euro</b> iPhone Air
  </a>
  <a class="result__snippet" href="//duckduckgo.com/l/?uddg=x">
     MediaMarkt verkauft das iPhone Air für 799 Euro statt 1.299 Euro.
  </a>
</div>
<div class="result results_links results_links_deep web-result">
  <a class="result__a" rel="nofollow"
     href="https://www.mydealz.de/deals/iphone-air-799">
     iPhone Air für 799 €
  </a>
  <a class="result__snippet">Abgelaufenes Angebot bei Amazon.</a>
</div>
"""


def test_build_query_joins_and_caps():
    query = build_query("iphone air wolkenweiß", "könnte es am black friday billiger werden?")
    assert query.startswith("iphone air wolkenweiß")
    assert "black friday" in query
    assert len(build_query("x", "y" * 500)) == 220


def test_parse_duckduckgo_unwraps_redirects_and_strips_markup():
    results = parse_duckduckgo(DDG_HTML)
    assert len(results) == 2
    assert results[0].url == "https://www.giga.de/tech/799-iphone"
    assert results[0].title == "799 Euro iPhone Air"
    assert "MediaMarkt" in results[0].snippet
    assert results[1].url == "https://www.mydealz.de/deals/iphone-air-799"


def test_parse_duckduckgo_drops_sponsored_results():
    html = """
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fduckduckgo.com%2Fy.js%3Fad_domain%3Dmediamarkt.de">
      Sponsored deal
    </a>
    <a class="result__snippet">Anzeige</a>
    """
    assert parse_duckduckgo(html) == []


def test_parse_searxng():
    payload = {
        "results": [
            {
                "title": "iPhone Air Black Friday",
                "url": "https://example.com/a",
                "content": "Ein <b>Angebot</b> für 799 Euro.",
            },
            {"title": "ohne URL", "content": "ignored"},
        ]
    }
    results = parse_searxng(payload)
    assert results == [
        SearchResult(
            title="iPhone Air Black Friday",
            url="https://example.com/a",
            snippet="Ein Angebot für 799 Euro.",
        )
    ]


class FakeResponse:
    def __init__(self, status=200, body=""):
        self.status = status
        self._body = body

    async def text(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSession:
    def __init__(self, status=200, body=""):
        self.status = status
        self.body = body
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params or {}, "headers": headers or {}})
        return FakeResponse(self.status, self.body)


def test_search_web_uses_duckduckgo(monkeypatch):
    async def fake_fetch(query, language):
        assert query == "iphone air"
        return DDG_HTML

    monkeypatch.setattr(websearch, "_fetch_duckduckgo", fake_fetch)
    session = FakeSession()
    results = asyncio.run(search_web(session, "iphone air", provider="duckduckgo"))
    assert len(results) == 2
    assert session.calls == []


def test_search_web_survives_a_failed_duckduckgo_fetch(monkeypatch):
    async def fake_fetch(query, language):
        return None

    monkeypatch.setattr(websearch, "_fetch_duckduckgo", fake_fetch)
    assert asyncio.run(search_web(FakeSession(), "iphone air", provider="duckduckgo")) == []


def test_search_web_uses_searxng_json():
    session = FakeSession(
        200,
        '{"results":[{"title":"A","url":"https://example.com","content":"s"}]}',
    )
    results = asyncio.run(
        search_web(
            session,
            "iphone air",
            provider="searxng",
            searxng_url="http://192.168.1.5:8080/",
        )
    )
    assert [result.url for result in results] == ["https://example.com"]
    assert session.calls[0]["url"] == "http://192.168.1.5:8080/search"
    assert session.calls[0]["params"]["format"] == "json"


def test_no_provider_and_empty_query_are_no_ops():
    session = FakeSession(200, DDG_HTML)
    assert asyncio.run(search_web(session, "", provider="duckduckgo")) == []
    assert asyncio.run(search_web(session, "x", provider="none")) == []
    assert session.calls == []
