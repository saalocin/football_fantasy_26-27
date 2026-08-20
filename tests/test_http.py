"""Tests for the polite HTTP client.

Every test drives a stubbed transport and a fake clock. Nothing here actually
sleeps or touches the network — a suite that waited out its own backoff would be
too slow to run, and one that reached the internet would fail for reasons that
have nothing to do with the code.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from fpl.lib.http import (
    USER_AGENT,
    NonRetryableStatus,
    RetriesExhausted,
    Throttle,
    fetch,
    parse_retry_after,
)


class FakeClock:
    """Monotonic time that only advances when something sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def monotonic(self) -> float:
        return self.now


def client_returning(*responses: httpx.Response | Exception) -> tuple[httpx.Client, list[str]]:
    """A client that plays back `responses` in order, recording each call."""
    calls: list[str] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        item = queue.pop(0) if queue else httpx.Response(200, content=b"fallback")
        if isinstance(item, Exception):
            raise item
        return item

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


# ---------------------------------------------------------------- happy path


def test_returns_body_exactly_as_served() -> None:
    """Bytes, not text. bronze takes a sha256 over exactly these."""
    body = b'{"total_players": 11000000}\r\n\xe2\x9a\xbd'
    client, _ = client_returning(httpx.Response(200, content=body))
    assert fetch("https://example.test/api/", client=client) == body


def test_sends_the_descriptive_user_agent() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, content=b"ok")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetch("https://example.test/", client=client)
    assert seen["user-agent"] == USER_AGENT
    assert "football_fantasy_26-27" in USER_AGENT, "the UA must say where to complain"


# ------------------------------------------------------------- non-retryable


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 418])
def test_other_4xx_raises_immediately_without_retrying(status: int) -> None:
    """A 404 does not become a 200 because you asked five times."""
    clock = FakeClock()
    client, calls = client_returning(httpx.Response(status))

    with pytest.raises(NonRetryableStatus) as caught:
        fetch("https://example.test/gone", client=client, sleep=clock.sleep)

    assert caught.value.status == status
    assert len(calls) == 1, f"retried a {status}"
    assert clock.slept == [], "slept before giving up on a non-retryable status"


# ----------------------------------------------------------------- retrying


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retryable_statuses_are_retried_then_succeed(status: int) -> None:
    clock = FakeClock()
    client, calls = client_returning(
        httpx.Response(status),
        httpx.Response(status),
        httpx.Response(200, content=b"finally"),
    )
    body = fetch(
        "https://example.test/flaky",
        client=client,
        sleep=clock.sleep,
        jitter=lambda ceiling: ceiling,
    )
    assert body == b"finally"
    assert len(calls) == 3
    assert clock.slept == [1.0, 2.0], "backoff should double"


def test_backoff_grows_exponentially_and_is_capped() -> None:
    clock = FakeClock()
    client, _ = client_returning(*[httpx.Response(500) for _ in range(6)])

    with pytest.raises(RetriesExhausted):
        fetch(
            "https://example.test/down",
            client=client,
            attempts=6,
            base_delay=1.0,
            max_delay=8.0,
            sleep=clock.sleep,
            jitter=lambda ceiling: ceiling,
        )
    # 1, 2, 4, 8, then capped at 8 rather than 16.
    assert clock.slept == [1.0, 2.0, 4.0, 8.0, 8.0]


def test_jitter_keeps_half_the_delay_and_randomises_the_rest() -> None:
    """Full jitter can return ~0 and defeat the backoff; equal jitter cannot."""
    clock = FakeClock()
    client, _ = client_returning(*[httpx.Response(500) for _ in range(4)])

    with pytest.raises(RetriesExhausted):
        fetch(
            "https://example.test/down",
            client=client,
            attempts=4,
            base_delay=4.0,
            sleep=clock.sleep,
        )
    ceilings = [4.0, 8.0, 16.0]
    for slept, ceiling in zip(clock.slept, ceilings, strict=True):
        assert ceiling / 2 <= slept <= ceiling


def test_transport_errors_are_retried() -> None:
    clock = FakeClock()
    client, calls = client_returning(
        httpx.ConnectError("refused"),
        httpx.Response(200, content=b"recovered"),
    )
    assert fetch("https://example.test/", client=client, sleep=clock.sleep) == b"recovered"
    assert len(calls) == 2


def test_exhausting_the_budget_names_the_last_failure() -> None:
    clock = FakeClock()
    client, calls = client_returning(*[httpx.Response(503) for _ in range(3)])

    with pytest.raises(RetriesExhausted) as caught:
        fetch("https://example.test/down", client=client, attempts=3, sleep=clock.sleep)

    assert caught.value.attempts == 3
    assert "503" in caught.value.last
    assert len(calls) == 3


# -------------------------------------------------------------- Retry-After


def test_retry_after_seconds_overrides_our_backoff() -> None:
    clock = FakeClock()
    client, _ = client_returning(
        httpx.Response(429, headers={"Retry-After": "37"}),
        httpx.Response(200, content=b"ok"),
    )
    fetch(
        "https://example.test/",
        client=client,
        sleep=clock.sleep,
        jitter=lambda ceiling: ceiling,
    )
    assert clock.slept == [37.0], "the host's own instruction should win"


def test_retry_after_is_still_capped_by_max_delay() -> None:
    """A hostile or mistaken Retry-After must not park the process forever."""
    clock = FakeClock()
    client, _ = client_returning(
        httpx.Response(503, headers={"Retry-After": "86400"}),
        httpx.Response(200, content=b"ok"),
    )
    fetch("https://example.test/", client=client, max_delay=30.0, sleep=clock.sleep)
    assert clock.slept == [30.0]


def test_unparseable_retry_after_falls_back_to_backoff() -> None:
    """Garbage must not be read as zero, which would mean hammering."""
    clock = FakeClock()
    client, _ = client_returning(
        httpx.Response(429, headers={"Retry-After": "soon-ish"}),
        httpx.Response(200, content=b"ok"),
    )
    fetch(
        "https://example.test/",
        client=client,
        base_delay=2.0,
        sleep=clock.sleep,
        jitter=lambda ceiling: ceiling,
    )
    assert clock.slept == [2.0]


def test_parse_retry_after_handles_http_dates() -> None:
    now = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
    assert parse_retry_after("Thu, 20 Aug 2026 12:00:30 GMT", now=now) == 30.0
    # A date already in the past means "now", never a negative sleep.
    assert parse_retry_after("Thu, 20 Aug 2026 11:59:00 GMT", now=now) == 0.0


@pytest.mark.parametrize("value", [None, "", "   ", "not-a-date"])
def test_parse_retry_after_rejects_what_it_cannot_read(value: str | None) -> None:
    assert parse_retry_after(value) is None


# ----------------------------------------------------------------- throttle


def test_throttle_puts_a_floor_under_the_gap_between_calls() -> None:
    clock = FakeClock()
    throttle = Throttle(1.5, sleep=clock.sleep, monotonic=clock.monotonic)

    throttle.wait("fantasy.premierleague.com")
    assert clock.slept == [], "the first call to a host never waits"

    throttle.wait("fantasy.premierleague.com")
    assert clock.slept == [1.5]


def test_throttle_is_per_host() -> None:
    """A slow host must not make us polite to an unrelated one."""
    clock = FakeClock()
    throttle = Throttle(1.0, sleep=clock.sleep, monotonic=clock.monotonic)

    throttle.wait("a.test")
    throttle.wait("b.test")
    assert clock.slept == []


def test_throttle_does_not_wait_when_enough_time_already_passed() -> None:
    clock = FakeClock()
    throttle = Throttle(1.0, sleep=clock.sleep, monotonic=clock.monotonic)

    throttle.wait("a.test")
    clock.now += 5.0
    throttle.wait("a.test")
    assert clock.slept == []


def test_fetch_throttles_every_attempt_including_retries() -> None:
    """Retries are exactly when politeness matters most."""
    clock = FakeClock()
    throttle = Throttle(1.0, sleep=clock.sleep, monotonic=clock.monotonic)
    client, calls = client_returning(
        httpx.Response(500),
        httpx.Response(200, content=b"ok"),
    )
    fetch(
        "https://example.test/",
        client=client,
        throttle=throttle,
        sleep=clock.sleep,
        jitter=lambda ceiling: ceiling,
    )
    assert len(calls) == 2
    # One backoff sleep; the throttle needed none on top because the backoff had
    # already carried us past the minimum interval.
    assert clock.slept == [1.0]
