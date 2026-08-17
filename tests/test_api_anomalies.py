from array import array
from datetime import date

import pytest

from app.api.deps import get_anomaly_board_service
from app.services import anomaly_board
from app.services.anomaly_board import AnomalyBoardService
from app.services.city_index import CityRecord
from app.services.normals import NormalsStore
from app.services.recent import Reading, RecentStore


@pytest.fixture
def board_app(app, fake_redis, monkeypatch):
    """Wire the endpoint onto synthetic cities, normals and readings.

    Nothing here stubs HTTP, because nothing in the read path makes a request.
    """
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
        lambda: NormalsStore.from_values(
            values, len(cities), window_start="2021-01-01", window_end="2025-12-31"
        ),
    )

    # Temperature and humidity deliberately rank the cities in opposite orders,
    # so a board that silently used one variable for both would be obvious.
    observed = date(2026, 6, 15)
    readings = {
        0: Reading(local_date=observed, temperature_c=30.0, humidity_pct=62.0),
        1: Reading(local_date=observed, temperature_c=28.0, humidity_pct=65.0),
        2: Reading(local_date=observed, temperature_c=26.0, humidity_pct=70.0),
        3: Reading(local_date=observed, temperature_c=24.0, humidity_pct=80.0),
    }
    monkeypatch.setattr(
        anomaly_board, "load_recent", lambda: RecentStore(readings=readings, as_of=observed)
    )

    service = AnomalyBoardService(fake_redis, 50, None)
    app.dependency_overrides[get_anomaly_board_service] = lambda: service
    return app


async def test_cold_start_returns_empty_board_not_an_error(client, board_app):
    """A dashboard must not break because a background job has not run yet."""
    response = await client.get("/anomalies")

    assert response.status_code == 200
    body = response.json()
    assert body["temperature"] == []
    assert body["humidity"] == []
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
    temperature = body["temperature"]
    assert [row["city"] for row in temperature] == ["City0", "City1", "City2", "City3"]
    assert [row["rank"] for row in temperature] == [1, 2, 3, 4]
    assert temperature[0]["z_score"] == 5.0
    assert temperature[0]["driver"] == "temperature"
    assert temperature[0]["direction"] == "above"

    # Humidity ranks the same cities in the opposite order, on its own variable.
    humidity = body["humidity"]
    assert [row["city"] for row in humidity] == ["City3", "City2", "City1", "City0"]
    assert humidity[0]["driver"] == "humidity"
    assert humidity[0]["z_score"] == 4.0


async def test_rows_carry_the_numbers_needed_to_audit_the_ranking(client, board_app):
    await client.post("/anomalies/refresh")
    row = (await client.get("/anomalies")).json()["temperature"][0]

    # (30.0 - 20.0) / 2.0 == 5.0, recomputable from the response alone.
    assert row["temperature_c"] == 30.0
    assert row["normal_temperature_c"] == 20.0
    assert row["sd_temperature_c"] == 2.0
    assert row["z_temperature"] == 5.0


async def test_limit_is_honoured(client, board_app):
    await client.post("/anomalies/refresh")
    body = (await client.get("/anomalies?limit=2")).json()
    # The limit is per board: asking for two gets two of each, not two in total.
    assert len(body["temperature"]) == 2
    assert len(body["humidity"]) == 2


@pytest.mark.parametrize("limit", [0, -1, 51])
async def test_limit_out_of_range_is_rejected(client, board_app, limit):
    assert (await client.get(f"/anomalies?limit={limit}")).status_code == 422


async def test_board_serves_without_a_briefing_when_no_key_is_configured(client, board_app):
    """The whole design in one assertion: ranking never depends on the LLM."""
    await client.post("/anomalies/refresh")
    body = (await client.get("/anomalies")).json()

    assert body["briefing"] is None
    assert len(body["temperature"]) == 4
    assert len(body["humidity"]) == 4
