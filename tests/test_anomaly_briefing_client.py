"""The interpretation layer, driven through a stubbed SDK.

No network: these assert the contract the sweep relies on -- that every failure
mode returns ``None`` instead of raising, and that the request asks for
interpretation rather than for arithmetic that already happened.
"""

import anthropic
import pytest

from app.clients.anthropic import SYSTEM_PROMPT, AnomalyBriefingClient
from app.models.anomaly import AnomalyBriefing, AnomalyRow, SynopticEvent


def row(city="Hong Kong", z=5.9, rank=1):
    return AnomalyRow(
        rank=rank,
        city=city,
        state="",
        country="HK",
        latitude=22.28,
        longitude=114.15,
        temperature_c=27.0,
        humidity_pct=62,
        normal_temperature_c=28.4,
        normal_humidity_pct=87.6,
        sd_temperature_c=1.1,
        sd_humidity_pct=4.3,
        z_temperature=-1.27,
        z_humidity=-z,
        z_score=z,
        driver="humidity",
        direction="below",
    )


BRIEFING = AnomalyBriefing(
    headline="A dry intrusion over the Pearl River Delta",
    events=[
        SynopticEvent(
            name="Pearl River Delta dry intrusion",
            cities=["Hong Kong", "Shenzhen"],
            explanation="One continental air mass, not five separate events.",
        )
    ],
    notes=[],
    suspect_readings=[],
)


class StubMessages:
    def __init__(self, result=None, error=None, stop_reason="end_turn"):
        self.result = result
        self.error = error
        self.stop_reason = stop_reason
        self.kwargs = None

    async def parse(self, **kwargs):
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error

        class Response:
            pass

        response = Response()
        response.parsed_output = self.result
        response.stop_reason = self.stop_reason
        return response


@pytest.fixture
def briefing_client(settings, monkeypatch):
    def build(**stub_kwargs):
        stub = StubMessages(**stub_kwargs)
        monkeypatch.setattr(
            "anthropic.AsyncAnthropic",
            lambda **_: type("C", (), {"messages": stub})(),
        )
        client = AnomalyBriefingClient(settings)
        return client, stub

    return build


async def test_returns_the_parsed_briefing(briefing_client):
    client, _ = briefing_client(result=BRIEFING)
    result = await client.brief([row()], [])
    assert result is not None
    assert result.events[0].cities == ["Hong Kong", "Shenzhen"]


async def test_empty_board_never_calls_the_api(briefing_client):
    client, stub = briefing_client(result=BRIEFING)
    assert await client.brief([], []) is None
    assert stub.kwargs is None


async def test_api_error_degrades_to_none(briefing_client):
    error = anthropic.APIError("down", request=None, body=None)
    client, _ = briefing_client(error=error)
    assert await client.brief([row()], []) is None


async def test_unexpected_error_degrades_to_none(briefing_client):
    client, _ = briefing_client(error=RuntimeError("something else entirely"))
    assert await client.brief([row()], []) is None


async def test_refusal_degrades_to_none(briefing_client):
    client, _ = briefing_client(result=BRIEFING, stop_reason="refusal")
    assert await client.brief([row()], []) is None


async def test_request_asks_for_structured_output_on_the_configured_model(
    briefing_client, settings
):
    client, stub = briefing_client(result=BRIEFING)
    await client.brief([row()], [])

    assert stub.kwargs["model"] == settings.anthropic_model
    assert stub.kwargs["output_format"] is AnomalyBriefing
    assert stub.kwargs["thinking"] == {"type": "adaptive"}


async def test_prompt_supplies_coordinates_and_forbids_recomputation(briefing_client):
    """Grouping is geographic, and the ranking is not up for renegotiation."""
    client, stub = briefing_client(result=BRIEFING)
    await client.brief([row()], [])

    content = stub.kwargs["messages"][0]["content"]
    assert '"lat": 22.28' in content
    assert '"z_score": 5.9' in content

    system = stub.kwargs["system"]
    assert system == SYSTEM_PROMPT
    assert "Do not recompute, re-rank" in system
