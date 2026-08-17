"""The spans the trace is read from.

These assert the two things a reader of a trace actually relies on: that the
model call is a span carrying model and token detail rather than an anonymous
POST, and that the sweep nests around it so the shape can be read at all. The
degradation guarantees are covered in test_anomaly_board.py and
test_anomaly_briefing_client.py; what is re-asserted here is only that adding
instrumentation did not quietly turn a failure into a raise.
"""

import anthropic
import pytest
from opentelemetry.trace import StatusCode

from app.clients.anthropic import AnomalyBriefingClient
from app.services import anomaly_board
from app.services.anomaly_board import AnomalyBoardService
from tests.test_anomaly_board import BRIEFING as BOARD_BRIEFING
from tests.test_anomaly_board import (
    StubBriefer,
    make_cities,
    make_normals,
    make_recent,
    reading,
)
from tests.test_anomaly_briefing_client import BRIEFING, StubMessages, row


def named(spans, name):
    """The one finished span with this name, or a failure naming what was there."""
    matches = [s for s in spans.get_finished_spans() if s.name == name]
    assert len(matches) == 1, (
        f"expected exactly one {name!r} span, got {len(matches)}; "
        f"saw {[s.name for s in spans.get_finished_spans()]}"
    )
    return matches[0]


@pytest.fixture
def briefing_client(settings, monkeypatch):
    """The real client, with only the SDK's network call replaced."""

    def build(**stub_kwargs):
        stub = StubMessages(**stub_kwargs)
        monkeypatch.setattr(
            "anthropic.AsyncAnthropic",
            lambda **_: type("C", (), {"messages": stub})(),
        )
        return AnomalyBriefingClient(settings), stub

    return build


@pytest.fixture
def patched(monkeypatch):
    def apply(n_cities, readings=None):
        cities = make_cities(n_cities)
        monkeypatch.setattr(anomaly_board, "city_records", lambda: cities)
        monkeypatch.setattr(anomaly_board, "load_normals", lambda: make_normals(n_cities))
        if readings is None:
            readings = [reading() for _ in range(n_cities)]
        store = make_recent(readings)
        monkeypatch.setattr(anomaly_board, "load_recent", lambda: store)
        return cities

    return apply


class TestModelSpan:
    async def test_carries_request_and_response_detail(self, briefing_client, spans):
        client, _ = briefing_client(result=BRIEFING)

        await client.brief([row()], [])

        span = named(spans, "chat claude-opus-5")
        assert span.attributes["gen_ai.operation.name"] == "chat"
        assert span.attributes["gen_ai.provider.name"] == "anthropic"
        assert span.attributes["gen_ai.request.model"] == "claude-opus-5"
        assert span.attributes["gen_ai.request.max_tokens"] == 4096
        assert span.attributes["gen_ai.response.model"] == "claude-opus-5"
        assert span.attributes["gen_ai.response.finish_reasons"] == ("end_turn",)
        assert span.attributes["gen_ai.usage.input_tokens"] == 4471
        assert span.attributes["gen_ai.usage.output_tokens"] == 806
        assert span.attributes["anthropic.thinking"] == "adaptive"
        assert span.attributes["anthropic.effort"] == "low"
        assert span.status.status_code is StatusCode.UNSET

    async def test_board_size_is_on_the_span(self, briefing_client, spans):
        """So a briefing can be read against how much it was given to work with."""
        client, _ = briefing_client(result=BRIEFING)

        await client.brief([row(), row(city="Shenzhen")], [row(city="Dhaka")])

        span = named(spans, "chat claude-opus-5")
        assert span.attributes["anomaly.briefing.temperature_rows"] == 2
        assert span.attributes["anomaly.briefing.humidity_rows"] == 1

    async def test_empty_board_emits_no_span_at_all(self, briefing_client, spans):
        """No call was made, so there is nothing to show as a call."""
        client, _ = briefing_client(result=BRIEFING)

        assert await client.brief([], []) is None
        assert spans.get_finished_spans() == ()


class TestFailuresStayNonFatal:
    async def test_refusal_is_an_error_span_but_not_an_exception(self, briefing_client, spans):
        """A decline is a policy outcome, not an outage -- error status, no exception."""
        client, _ = briefing_client(result=BRIEFING, stop_reason="refusal")

        assert await client.brief([row()], []) is None

        span = named(spans, "chat claude-opus-5")
        assert span.status.status_code is StatusCode.ERROR
        assert span.attributes["gen_ai.response.finish_reasons"] == ("refusal",)
        assert span.events == ()

    async def test_api_error_is_recorded_and_still_degrades(self, briefing_client, spans):
        client, _ = briefing_client(error=anthropic.APIError("down", request=None, body=None))

        assert await client.brief([row()], []) is None

        span = named(spans, "chat claude-opus-5")
        assert span.status.status_code is StatusCode.ERROR
        assert [e.name for e in span.events] == ["exception"]

    async def test_a_response_without_usage_still_serves(self, briefing_client, spans):
        """Missing token counts must cost the attributes, not the briefing."""
        client, _ = briefing_client(result=BRIEFING, usage=None)

        assert await client.brief([row()], []) is not None

        span = named(spans, "chat claude-opus-5")
        assert "gen_ai.usage.input_tokens" not in span.attributes
        assert span.status.status_code is StatusCode.UNSET


class TestSweepShape:
    async def test_the_model_call_nests_under_the_sweep(
        self, fake_redis, patched, briefing_client, spans
    ):
        """The whole point of the trace: one tree, model call included."""
        patched(1, readings=[reading(temp=28.0)])
        briefer, _ = briefing_client(result=BRIEFING)
        service = AnomalyBoardService(fake_redis, 50, briefer)

        await service.sweep()

        sweep = named(spans, "anomaly.sweep")
        briefing = named(spans, "anomaly.briefing")
        model = named(spans, "chat claude-opus-5")

        assert sweep.parent is None
        assert briefing.parent.span_id == sweep.context.span_id
        assert model.parent.span_id == briefing.context.span_id
        assert named(spans, "anomaly.score").parent.span_id == sweep.context.span_id

    async def test_sweep_span_carries_the_counts(self, fake_redis, patched, spans):
        patched(3, readings=[reading(temp=28.0), reading(temp=27.0), reading()])
        service = AnomalyBoardService(fake_redis, 50)

        await service.sweep()

        sweep = named(spans, "anomaly.sweep")
        assert sweep.attributes["cities.covered"] == 3
        assert sweep.attributes["cities.in_index"] == 3
        # Two, not three: City2 is exactly normal and never scores. The gap
        # between covered and scored is the point of carrying both.
        assert sweep.attributes["cities.scored"] == 2
        assert sweep.attributes["board.temperature_rows"] == 2

    async def test_a_cache_hit_explains_the_missing_model_span(
        self, fake_redis, patched, spans
    ):
        """A sweep with no model call under it is the cache, not a silent failure."""
        patched(1, readings=[reading(temp=28.0)])
        briefer = StubBriefer(result=BOARD_BRIEFING)

        first = AnomalyBoardService(fake_redis, 50, briefer)
        await first.sweep()
        assert named(spans, "anomaly.briefing").attributes["briefing.cache_hit"] is False

        spans.clear()
        second = AnomalyBoardService(fake_redis, 50, briefer)
        await second.sweep()

        assert briefer.calls == 1
        assert named(spans, "anomaly.briefing").attributes["briefing.cache_hit"] is True

    async def test_bulk_fetch_reports_missing_readings(self, spans, settings):
        """Batches that degraded to None issue no request, so nothing else counts them."""
        from app.clients.open_meteo import OpenMeteoClient

        class DeadHttp:
            async def get(self, *args, **kwargs):
                raise __import__("httpx").ConnectError("nope")

        client = OpenMeteoClient(DeadHttp(), settings)
        assert await client.fetch_current_bulk([(1.0, 2.0), (3.0, 4.0)]) == [None, None]

        span = named(spans, "open_meteo.fetch_current_bulk")
        assert span.attributes["coordinates.count"] == 2
        assert span.attributes["readings.missing"] == 2
