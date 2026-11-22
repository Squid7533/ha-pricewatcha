"""Tests for the optional AI advisor: config, client parsing, grounding and budget."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.pricewatcha.websearch import SearchResult

from custom_components.pricewatcha.ai import (
    AIAdvisor,
    AIClient,
    AIConfig,
    AIAuthError,
    AIRateLimitError,
    AIResponseError,
    AIResult,
    AIUsage,
    ACTION_TOOLS,
    AIPlan,
    AIToolCall,
    AIToolsUnsupported,
    endpoint_for,
    estimate_cost,
    unknown_price,
)

PAYLOAD = {
    "query": "iphone air wolkenweiß",
    "matched": 14,
    "compared": 10,
    "deals": [
        {
            "price": 669.0,
            "currency": "EUR",
            "shop": "Asgoodasnew",
            "condition": "very good",
            "capacity": "256 GB",
            "vs_average_pct": 4.3,
            "average_price": 699.0,
            "historical_low": 669.0,
        },
        {
            "price": 699.0,
            "currency": "EUR",
            "shop": "Asgoodasnew",
            "condition": "like new",
            "capacity": "256 GB",
            "vs_average_pct": 3.9,
            "average_price": 727.0,
            "historical_low": 699.0,
        },
    ],
    "groups": [
        {
            "capacity": "256 GB",
            "offers": [
                {"condition": "very good", "price": 669.0, "shop": "Asgoodasnew"},
                {"condition": "like new", "price": 699.0, "shop": "Asgoodasnew"},
                {"condition": "new", "price": 889.0, "shop": "Asgoodasnew"},
            ],
        },
        {
            "capacity": "512 GB",
            "offers": [
                {"condition": "like new", "price": 1019.0, "shop": "Asgoodasnew"},
            ],
        },
    ],
}


class FakeResponse:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSession:
    def __init__(self, status: int = 200, body: str = "{}") -> None:
        self.status = status
        self.body = body
        self.calls: list[dict] = []

    def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers or {}})
        return FakeResponse(self.status, self.body)


class FakeClient:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0
        self.web_search = False

    async def async_complete(
        self, system: str, user: str, *, web_search: bool = False
    ) -> AIResult:
        self.calls += 1
        self.web_search = web_search
        self.user = user
        self.system = system
        return AIResult(
            text=self.text,
            model="fake-model",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.0001,
        )


class FakeUsage:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.recorded = 0.0

    async def async_can_spend(self, estimate: float):
        return self.allowed, "" if self.allowed else "daily budget"

    async def async_record(self, cost: float) -> float:
        self.recorded += cost
        return 0.01


def config(**overrides) -> AIConfig:
    cfg = AIConfig(
        enabled=True,
        base_url="https://api.deepseek.com",
        model="deepseek-flash",
        api_key="sk-test",
        language="de",
        input_cost_per_mtok=0.15,
        output_cost_per_mtok=0.60,
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def test_defaults_are_off_and_point_at_deepseek():
    cfg = AIConfig.from_options({})
    assert cfg.enabled is False
    assert cfg.base_url == "https://api.deepseek.com"
    assert cfg.model == "deepseek-flash"
    assert cfg.language == "en"
    assert cfg.daily_budget_usd == 1.0
    assert cfg.monthly_budget_usd == 10.0
    assert cfg.web_search is True


def test_options_are_parsed_and_unknown_provider_falls_back():
    cfg = AIConfig.from_options(
        {
            "ai_enabled": True,
            "ai_provider": "nonsense",
            "ai_base_url": " http://localhost:11434/v1 ",
            "ai_model": " llama3.1 ",
            "ai_language": "en",
            "ai_max_tokens": "800",
            "ai_temperature": "0.2",
        }
    )
    assert cfg.provider == "openai_compatible"
    assert cfg.base_url == "http://localhost:11434/v1"
    assert cfg.model == "llama3.1"
    assert cfg.language == "en"
    assert cfg.max_tokens == 800
    assert cfg.temperature == 0.2


def test_endpoints():
    assert (
        endpoint_for(config()) == "https://api.deepseek.com/chat/completions"
    )
    assert (
        endpoint_for(config(base_url="https://api.openai.com/v1/"))
        == "https://api.openai.com/v1/chat/completions"
    )
    assert (
        endpoint_for(config(provider="anthropic", base_url="https://api.anthropic.com"))
        == "https://api.anthropic.com/v1/messages"
    )
    assert (
        endpoint_for(
            config(provider="anthropic", base_url="https://api.deepseek.com/anthropic")
        )
        == "https://api.deepseek.com/anthropic/v1/messages"
    )


def test_openai_request_and_parse():
    body = (
        '{"model":"deepseek-flash","choices":[{"message":{"content":"669 Euro sind gut."}}],'
        '"usage":{"prompt_tokens":120,"completion_tokens":42}}'
    )
    session = FakeSession(200, body)
    client = AIClient(session, config())

    result = asyncio.run(client.async_complete("system", "user"))

    assert result.text == "669 Euro sind gut."
    assert result.input_tokens == 120
    assert result.output_tokens == 42
    assert result.cost_usd == pytest.approx((120 * 0.15 + 42 * 0.60) / 1_000_000)
    call = session.calls[0]
    assert call["url"] == "https://api.deepseek.com/chat/completions"
    assert call["headers"]["authorization"] == "Bearer sk-test"
    assert call["json"]["model"] == "deepseek-flash"
    assert call["json"]["messages"][0]["role"] == "system"
    assert call["json"]["max_tokens"] == 700
    assert call["json"]["thinking"] == {"type": "disabled"}


def test_anthropic_request_and_parse():
    body = (
        '{"model":"claude-test","content":[{"type":"text","text":"Take it."}],'
        '"usage":{"input_tokens":90,"output_tokens":10}}'
    )
    session = FakeSession(200, body)
    client = AIClient(
        session, config(provider="anthropic", base_url="https://api.anthropic.com")
    )

    result = asyncio.run(client.async_complete("system", "user"))

    assert result.text == "Take it."
    call = session.calls[0]
    assert call["url"] == "https://api.anthropic.com/v1/messages"
    assert call["headers"]["x-api-key"] == "sk-test"
    assert call["headers"]["anthropic-version"]
    assert call["json"]["system"] == "system"


def test_http_errors_are_typed():
    with pytest.raises(AIAuthError):
        asyncio.run(
            AIClient(FakeSession(401, "nope"), config()).async_complete("s", "u")
        )
    with pytest.raises(AIRateLimitError):
        asyncio.run(
            AIClient(FakeSession(429, "slow down"), config()).async_complete("s", "u")
        )
    with pytest.raises(AIResponseError):
        asyncio.run(
            AIClient(FakeSession(500, "boom"), config()).async_complete("s", "u")
        )


def test_unknown_price_is_detected():
    assert unknown_price("Bei Saturn gibt es das für 799 €.", PAYLOAD) == 799
    assert unknown_price("Das Gerät kostet 1.299 Euro.", PAYLOAD) == 1299
    assert unknown_price("Der Preis liegt bei 669 €.", PAYLOAD) is None
    assert unknown_price("It costs 799 $ elsewhere.", PAYLOAD) == 799


def test_derived_premiums_and_spaced_thousands_are_allowed():
    assert unknown_price("30 € mehr für wie neu, 220 € für neu.", PAYLOAD) is None
    assert unknown_price("Die 512-GB-Version kostet 1 019 €.", PAYLOAD) is None
    assert unknown_price("Rund 670 € sind fair.", PAYLOAD) is None


def test_estimate_is_a_worst_case():
    estimate = estimate_cost(config(), "x" * 4000, "y" * 4000)
    assert estimate > 0
    assert estimate < 0.01


def test_advisor_returns_grounded_text_and_records_cost():
    client = FakeClient("669 € sind der beste Preis; 30 € mehr für wie neu lohnen sich.")
    usage = FakeUsage()

    result = asyncio.run(
        AIAdvisor(None, config(), client=client, usage=usage).async_advise(PAYLOAD)
    )

    assert result is not None
    assert "669 €" in result.text
    assert result.spent_today == 0.01
    assert usage.recorded == pytest.approx(0.0001)


def test_advisor_drops_a_hallucinated_price():
    client = FakeClient("Bei Saturn gibt es das für 799 €.")

    result = asyncio.run(
        AIAdvisor(None, config(), client=client, usage=FakeUsage()).async_advise(PAYLOAD)
    )

    assert result is None


def test_advisor_stops_before_calling_when_the_budget_is_gone():
    client = FakeClient("egal")
    usage = FakeUsage(allowed=False)
    advisor = AIAdvisor(None, config(), client=client, usage=usage)

    assert asyncio.run(advisor.async_advise(PAYLOAD)) is None
    assert client.calls == 0
    assert advisor.last_error == "budget_daily"


def test_disabled_advisor_does_nothing():
    advisor = AIAdvisor(None, config(enabled=False), client=FakeClient("x"), usage=FakeUsage())
    assert advisor.available is False
    assert asyncio.run(advisor.async_advise(PAYLOAD)) is None
    assert advisor.last_error == "not_configured"


def test_question_is_passed_to_the_model():
    class RecordingClient(FakeClient):
        def __init__(self):
            super().__init__("Antwort.")
            self.user = ""

        async def async_complete(
            self, system: str, user: str, *, web_search: bool = False
        ) -> AIResult:
            self.user = user
            return await super().async_complete(
                system, user, web_search=web_search
            )

    client = RecordingClient()
    asyncio.run(
        AIAdvisor(None, config(), client=client, usage=FakeUsage()).async_advise(
            PAYLOAD, "Ist 669 gut?"
        )
    )

    assert "Ist 669 gut?" in client.user
    assert "Frage des Nutzers" in client.user


class FakeStore:
    def __init__(self):
        self.data = None

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data


def test_usage_resets_and_enforces_the_daily_budget():
    store = FakeStore()
    cfg = config(daily_budget_usd=0.001, monthly_budget_usd=0)
    usage = AIUsage(None, cfg, store=store)

    allowed, reason = asyncio.run(usage.async_can_spend(0.0006))
    assert allowed is True and reason == ""

    asyncio.run(usage.async_record(0.0006))
    allowed, reason = asyncio.run(usage.async_can_spend(0.0006))
    assert allowed is False
    assert reason == "daily budget"
    assert store.data["spent_today"] == pytest.approx(0.0006)


def test_a_zero_budget_means_no_limit():
    usage = AIUsage(None, config(daily_budget_usd=0, monthly_budget_usd=0), store=FakeStore())
    asyncio.run(usage.async_record(100.0))
    allowed, _ = asyncio.run(usage.async_can_spend(100.0))
    assert allowed is True


def test_web_question_allows_external_prices_and_uses_the_tool():
    client = FakeClient("Bei MediaMarkt gab es das Gerät zuletzt für 799 €.")
    advisor = AIAdvisor(
        None, config(web_search=True), client=client, usage=FakeUsage()
    )

    result = asyncio.run(
        advisor.async_advise(PAYLOAD, "Könnte es am Black Friday besser werden?", web=True)
    )

    assert result is not None
    assert client.web_search is True
    assert "799" in result.text


def test_assessment_still_rejects_external_prices():
    client = FakeClient("Bei MediaMarkt kostet es 799 €.")

    result = asyncio.run(
        AIAdvisor(None, config(web_search=True), client=client, usage=FakeUsage()).async_advise(
            PAYLOAD
        )
    )

    assert result is None
    assert client.web_search is False


def test_question_prompt_contains_date_and_history():
    class RecordingClient(FakeClient):
        def __init__(self):
            super().__init__("Antwort.")
            self.user = ""

        async def async_complete(
            self, system: str, user: str, *, web_search: bool = False
        ) -> AIResult:
            self.user = user
            return await super().async_complete(system, user, web_search=web_search)

    payload = dict(PAYLOAD)
    payload["deals"] = [dict(PAYLOAD["deals"][0], history=[{"date": "2026-10-13", "price": 739}])]
    client = RecordingClient()
    asyncio.run(
        AIAdvisor(None, config(), client=client, usage=FakeUsage()).async_advise(
            payload, "Wird es billiger?", None, web=False
        )
    )

    assert "Heute ist der" in client.user
    assert "2026-10-13" in client.user
    assert "Black Friday" not in client.user.split("Frage des Nutzers")[0]


def test_web_search_request_declares_the_tool_and_parses_citations():
    body = (
        '{"model":"deepseek-flash","choices":[{"message":{'
        '"content":"Bei MediaMarkt 799 €.",'
        '"annotations":[{"type":"url_citation","url_citation":{'
        '"url":"https://example.com/deal","title":"Black Friday deal"}}]}}],'
        '"usage":{"prompt_tokens":900,"completion_tokens":80}}'
    )
    session = FakeSession(200, body)
    client = AIClient(session, config())

    result = asyncio.run(
        client.async_complete("system", "user", web_search=True)
    )

    call = session.calls[0]
    assert call["json"]["tools"] == [{"type": "deepseek_web_search"}]
    assert result.citations == [
        {"url": "https://example.com/deal", "title": "Black Friday deal"}
    ]


def test_without_web_search_no_tools_are_declared():
    session = FakeSession(200, '{"choices":[{"message":{"content":"ok"}}]}')
    asyncio.run(AIClient(session, config()).async_complete("s", "u"))
    assert "tools" not in session.calls[0]["json"]


def test_a_rejected_web_tool_falls_back_to_a_catalog_only_answer():
    class WebRejectingClient(FakeClient):
        def __init__(self):
            super().__init__("Antwort ohne Web.")
            self.attempts = 0

        async def async_complete(self, system, user, *, web_search=False):
            self.attempts += 1
            if web_search:
                raise AIResponseError("HTTP 400: tools not supported")
            return await super().async_complete(system, user, web_search=web_search)

    client = WebRejectingClient()
    result = asyncio.run(
        AIAdvisor(None, config(), client=client, usage=FakeUsage()).async_advise(
            PAYLOAD, "Black Friday?", None, web=True
        )
    )

    assert result is not None
    assert result.text == "Antwort ohne Web."
    assert client.attempts == 2


class FakeSearcher:
    def __init__(self, results):
        self.results = results
        self.calls: list[str] = []

    async def __call__(self, query: str):
        self.calls.append(query)
        return self.results


def test_external_search_feeds_results_and_citations_for_any_provider():
    searcher = FakeSearcher(
        [
            SearchResult(
                title="GIGA: 799 Euro",
                url="https://example.com/giga",
                snippet="MediaMarkt 799 Euro",
            )
        ]
    )
    client = FakeClient("Bei MediaMarkt 799 €, die Quellen zeigen es.")
    advisor = AIAdvisor(
        None,
        config(
            base_url="http://localhost:11434/v1",
            model="llama3.1",
            web_search_provider="duckduckgo",
        ),
        client=client,
        usage=FakeUsage(),
        searcher=searcher,
    )

    result = asyncio.run(
        advisor.async_advise(PAYLOAD, "Black Friday?", None, web=True)
    )

    assert result is not None
    assert client.web_search is False, "external results are passed in, no native tool"
    assert "https://example.com/giga" in client.user
    assert result.citations == [
        {"url": "https://example.com/giga", "title": "GIGA: 799 Euro"}
    ]
    assert searcher.calls == ["iphone air wolkenweiß Black Friday?"]


def test_external_search_results_are_cached():
    searcher = FakeSearcher([SearchResult("A", "https://example.com/a")])
    advisor = AIAdvisor(
        None,
        config(
            base_url="http://localhost:11434/v1",
            model="llama3.1",
            web_search_provider="duckduckgo",
        ),
        client=FakeClient("Antwort."),
        usage=FakeUsage(),
        searcher=searcher,
    )

    asyncio.run(advisor.async_advise(PAYLOAD, "Black Friday?", None, web=True))
    asyncio.run(advisor.async_advise(PAYLOAD, "Black Friday?", None, web=True))

    assert searcher.calls == ["iphone air wolkenweiß Black Friday?"]


def test_external_search_failure_falls_back_to_the_catalog_answer():
    searcher = FakeSearcher([])
    client = FakeClient("Antwort ohne Web.")
    advisor = AIAdvisor(
        None,
        config(
            base_url="http://localhost:11434/v1",
            model="llama3.1",
            web_search_provider="duckduckgo",
        ),
        client=client,
        usage=FakeUsage(),
        searcher=searcher,
    )

    result = asyncio.run(
        advisor.async_advise(PAYLOAD, "Black Friday?", None, web=True)
    )

    assert result is not None
    assert result.citations == []
    assert "Web search results" not in client.user


def test_a_simple_price_question_does_not_search():
    searcher = FakeSearcher([SearchResult("A", "https://example.com/a")])
    advisor = AIAdvisor(
        None,
        config(
            base_url="http://localhost:11434/v1",
            model="llama3.1",
            web_search_provider="duckduckgo",
        ),
        client=FakeClient("Antwort aus den Daten."),
        usage=FakeUsage(),
        searcher=searcher,
    )

    result = asyncio.run(
        advisor.async_advise(PAYLOAD, "Ist 669 ein guter Preis?", None, web=True)
    )

    assert result is not None
    assert searcher.calls == []
    assert result.citations == []


def test_history_is_included_in_the_question_prompt():
    class RecordingClient(FakeClient):
        def __init__(self):
            super().__init__("Antwort.")
            self.user = ""

        async def async_complete(
            self, system: str, user: str, *, web_search: bool = False
        ) -> AIResult:
            self.user = user
            return await super().async_complete(system, user, web_search=web_search)

    client = RecordingClient()
    history = [{"q": "Ist 669 gut?", "a": "Ja, das ist der Tiefstpreis."}]
    asyncio.run(
        AIAdvisor(None, config(), client=client, usage=FakeUsage()).async_advise(
            PAYLOAD, "Und Black Friday?", None, history=history
        )
    )

    assert "Gesprächsverlauf" in client.user
    assert "Ist 669 gut?" in client.user
    assert "Ja, das ist der Tiefstpreis." in client.user


def test_plan_parses_function_tool_calls():
    body = (
        '{"model":"deepseek-flash","choices":[{"message":{"content":null,'
        '"tool_calls":[{"id":"c1","type":"function","function":{'
        '"name":"watch","arguments":"{\\"ref\\":\\"w1\\"}"}}]}}],'
        '"usage":{"prompt_tokens":300,"completion_tokens":20}}'
    )
    session = FakeSession(200, body)
    client = AIClient(session, config())

    plan = asyncio.run(client.async_plan("system", "user", ACTION_TOOLS))

    assert plan.calls[0].name == "watch"
    assert plan.calls[0].arguments == {"ref": "w1"}
    call = session.calls[0]
    assert call["json"]["tool_choice"] == "auto"
    assert call["json"]["tools"] == ACTION_TOOLS


def test_plan_requires_an_openai_compatible_provider():
    client = AIClient(
        FakeSession(200, "{}"),
        config(provider="anthropic", base_url="https://api.anthropic.com"),
    )
    with pytest.raises(AIToolsUnsupported):
        asyncio.run(client.async_plan("s", "u", ACTION_TOOLS))


class FakePlanClient:
    def __init__(self, plan=None, error=None):
        self.plan = plan
        self.error = error
        self.calls = 0

    async def async_plan(self, system, user, tools):
        self.calls += 1
        if self.error:
            raise self.error
        return self.plan


def test_advisor_plan_records_cost_and_returns_calls():
    plan = AIPlan(
        calls=[AIToolCall("watch", {"ref": "w1"})],
        input_tokens=300,
        output_tokens=20,
    )
    usage = FakeUsage()
    advisor = AIAdvisor(
        None, config(), client=FakePlanClient(plan), usage=usage
    )

    result = asyncio.run(
        advisor.async_plan("beobachte w1", {"offers": [{"ref": "w1"}]})
    )

    assert result is plan
    assert usage.recorded > 0
    assert advisor.last_error is None


def test_advisor_plan_reports_missing_tool_support():
    advisor = AIAdvisor(
        None,
        config(),
        client=FakePlanClient(error=AIToolsUnsupported("no tools")),
        usage=FakeUsage(),
    )
    assert asyncio.run(advisor.async_plan("beobachte w1", {})) is None
    assert advisor.last_error == "tools_unsupported"
