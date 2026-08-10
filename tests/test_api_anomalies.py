from array import array

import pytest

from app.api.deps import get_anomaly_board_service
from app.clients.open_meteo import CurrentReading
from app.services import anomaly_board
from app.services.anomaly_board import AnomalyBoardService
from app.services.city_index import CityRecord
from app.services.normals import NormalsStore


class StubClient:
    def __init__(self, readings):
        self._readings = readings

    async def fetch_current_bulk(self, coordinates):
        return self._readings


@pytest.fixture
def board_app(app, fake_redis, monkeypatch):
    """Wire the endpoint onto synthetic cities so it never touches upstream."""
    cities = [
        CityRecord(
            row_index=i,
            geonameid=1000 + i,
            name=f"City{i}",
            state="",
            country="XX",
            population=10_000 - i,
            latitude=float(i),
            longitude=float(i),
        )
        for i in range(4)
    ]
    monkeypatch.setattr(anomaly_board, "city_records", lambda: cities)

    values = array("f")
    for _ in range(len(cities)):
        for _ in range(12):
            values.extend([20.0, 2.0, 60.0, 5.0])
    monkeypatch.setattr(
        anomaly_board,
        "load_normals",
        lambda: NormalsStore(
            values=values,
            n_cities=len(cities),
            window_start="2021-01-01",
            window_end="2025-12-31",
            cities_covered=len(cities),
        ),
    )

    readings = [
        CurrentReading(temperature_c=30.0, humidity_pct=60.0, local_date="2026-06-15"),
        CurrentReading(temperature_c=28.0, humidity_pct=60.0, local_date="2026-06-15"),
        CurrentReading(temperature_c=26.0, humidity_pct=60.0, local_date="2026-06-15"),
        CurrentReading(temperature_c=24.0, humidity_pct=60.0, local_date="2026-06-15"),
    ]
    service = AnomalyBoardService(fake_redis, StubClient(readings), 50, None)
    app.dependency_overrides[get_anomaly_board_service] = lambda: service
    return app


async def test_cold_start_returns_empty_board_not_an_error(client, board_app):
    """A dashboard must not break because a background job has not run yet."""
    response = await client.get("/anomalies")

    assert response.status_code == 200
    body = response.json()
    assert body["rows"] == []
    assert body["source"] == "unavailable"
    assert body["swept_at"] is None
    assert body["briefing"] is None


async def test_refresh_then_read_serves_a_ranked_board(client, board_app):
    refreshed = await client.post("/anomalies/refresh")
    assert refreshed.status_code == 200
    assert refreshed.json()["source"] == "fresh"

    response = await client.get("/anomalies")
    body = response.json()

    assert body["source"] == "fresh"
    assert [row["city"] for row in body["rows"]] == ["City0", "City1", "City2", "City3"]
    assert [row["rank"] for row in body["rows"]] == [1, 2, 3, 4]
    assert body["rows"][0]["z_score"] == 5.0
    assert body["rows"][0]["driver"] == "temperature"
    assert body["rows"][0]["direction"] == "above"


async def test_rows_carry_the_numbers_needed_to_audit_the_ranking(client, board_app):
    await client.post("/anomalies/refresh")
    row = (await client.get("/anomalies")).json()["rows"][0]

    # (30.0 - 20.0) / 2.0 == 5.0, recomputable from the response alone.
    assert row["temperature_c"] == 30.0
    assert row["normal_temperature_c"] == 20.0
    assert row["sd_temperature_c"] == 2.0
    assert row["z_temperature"] == 5.0


async def test_limit_is_honoured(client, board_app):
    await client.post("/anomalies/refresh")
    body = (await client.get("/anomalies?limit=2")).json()
    assert len(body["rows"]) == 2


@pytest.mark.parametrize("limit", [0, -1, 51])
async def test_limit_out_of_range_is_rejected(client, board_app, limit):
    assert (await client.get(f"/anomalies?limit={limit}")).status_code == 422


async def test_board_serves_without_a_briefing_when_no_key_is_configured(client, board_app):
    """The whole design in one assertion: ranking never depends on the LLM."""
    await client.post("/anomalies/refresh")
    body = (await client.get("/anomalies")).json()

    assert body["briefing"] is None
    assert len(body["rows"]) == 4
