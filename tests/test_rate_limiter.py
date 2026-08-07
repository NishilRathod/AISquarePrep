import pytest

from app.clients.rate_limiter import AsyncTokenBucket


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def sleep_calls(monkeypatch):
    calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        calls.append(seconds)

    monkeypatch.setattr("app.clients.rate_limiter.asyncio.sleep", fake_sleep)
    return calls


@pytest.mark.asyncio
async def test_acquires_within_capacity_do_not_sleep(sleep_calls):
    clock = FakeClock()
    bucket = AsyncTokenBucket(rate_per_minute=3, clock=clock)

    for _ in range(3):
        await bucket.acquire()

    assert sleep_calls == []


@pytest.mark.asyncio
async def test_acquire_beyond_capacity_sleeps_for_expected_duration(sleep_calls):
    clock = FakeClock()
    bucket = AsyncTokenBucket(rate_per_minute=3, clock=clock)

    for _ in range(3):
        await bucket.acquire()

    await bucket.acquire()

    assert len(sleep_calls) == 1
    assert sleep_calls[0] == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_refill_after_elapsed_time_avoids_further_sleep(sleep_calls):
    clock = FakeClock()
    bucket = AsyncTokenBucket(rate_per_minute=3, clock=clock)

    for _ in range(3):
        await bucket.acquire()

    clock.advance(20.0)
    await bucket.acquire()

    assert sleep_calls == []
