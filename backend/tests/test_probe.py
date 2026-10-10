import asyncio

import pytest

from app.probe import Probe, Unit


class Clock:
    def __init__(self):
        self.t = 5.0

    def now(self):
        return self.t


class Client:
    queued = set()
    stored = set()

    async def queued_ids(self, prefix):
        return self.queued

    async def stored_ids(self, prefix):
        return self.stored


def make():
    client = Client()
    probe = Probe(client, Clock(), 2, "test")
    unit = Unit(0, "cs-test-0", 0, done=1, code=202, latency_ms=1000)
    probe.units = [unit]
    return probe, client, unit


@pytest.mark.parametrize("code", [200, 201, 202, 204, 299])
def test_successful_http_codes_are_acknowledged_not_errors(code):
    unit = Unit(0, "a", 0, done=1, code=code)
    assert unit.acked and unit.state() == "queued"
    assert not unit.good()  # HTTP success alone is not business-level success


@pytest.mark.parametrize("code", [None, 199, 300, 400, 500])
def test_unsuccessful_http_codes_are_errors(code):
    assert Unit(0, "a", 0, done=1, code=code).state() == "error"


@pytest.mark.parametrize("rate", [0, -1, float("nan"), float("inf")])
def test_invalid_probe_rates_are_rejected(rate):
    with pytest.raises(ValueError):
        Probe(Client(), Clock(), rate, "invalid")


def test_final_poll_requires_two_absent_observations():
    async def go():
        p, _, u = make()
        await p.poll(final=True)
        assert not u.lost
        await p.poll(final=True)
        assert u.lost
    asyncio.run(go())


@pytest.mark.parametrize("unavailable", ["queued", "stored"])
def test_unknown_observations_break_consecutive_loss_evidence(unavailable):
    async def go():
        p, client, u = make()
        await p.poll()
        setattr(client, unavailable, None)
        await p.poll()
        setattr(client, unavailable, set())
        await p.poll()
        assert not u.lost
        await p.poll()
        assert u.lost
    asyncio.run(go())


def test_queue_reappearance_clears_a_previous_loss_classification():
    async def go():
        p, client, u = make()
        await p.poll()
        await p.poll()
        assert u.lost
        client.queued = {u.id}
        await p.poll()
        assert not u.lost and u.state() == "queued"
    asyncio.run(go())


def test_canceled_requests_keep_their_elapsed_latency():
    p, _, _ = make()
    p.units = [Unit(0, "pending", 1)]
    asyncio.run(p.close())
    assert p.units[0].latency_ms == 4000
    assert p.window(5)["error_rate"] == 1
    assert p.window(5)["p95_ms"] == 4000


def test_rolling_error_window_is_not_the_whole_run_percentage():
    p, _, _ = make()
    p.units = [Unit(i, str(i), 0, done=1, code=200, latency_ms=100) for i in range(10)]
    p.units += [Unit(i, str(i), 4, done=5, code=500, latency_ms=100) for i in range(10, 15)]
    assert p.window(5)["error_rate"] == 1
    assert p.counters()["errors"] / p.counters()["sent"] == pytest.approx(1 / 3)


def test_failed_scheduled_tasks_are_not_silently_ignored():
    async def go():
        p, _, _ = make()
        async def fail():
            raise RuntimeError("injection failed")
        task = asyncio.create_task(fail())
        p.monitor(task)
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="injection failed"):
            p.ensure_healthy()
    asyncio.run(go())
