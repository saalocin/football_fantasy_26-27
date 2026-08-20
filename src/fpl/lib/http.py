"""The only module in this project allowed to open a socket.

Rate limiting written per-fetcher is rate limiting that is wrong in one of them.
Putting the etiquette here makes it a property of the project rather than of
whoever wrote the most recent script.

Three concerns, and nothing else:

* a **User-Agent** that says who we are and where to complain,
* a **Throttle** that puts a floor under the gap between requests to one host,
* a **retry** that backs off, jitters, and honours ``Retry-After``.

Everything returns bytes exactly as served. No parsing, no decoding, no
normalising — that would be bronze doing silver's job, and it would break the
sha256 that :mod:`fpl.lib.bronze` takes over these same bytes.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

__all__ = [
    "DEFAULT_ATTEMPTS",
    "DEFAULT_MIN_INTERVAL",
    "USER_AGENT",
    "HttpError",
    "NonRetryableStatus",
    "RetriesExhausted",
    "Throttle",
    "fetch",
    "parse_retry_after",
]

#: Identifies this project to every host we touch. The FPL API is undocumented
#: and *tolerated* rather than published, so saying who we are and where to
#: complain is the cheapest thing we can do to stay tolerated.
USER_AGENT = (
    "fpl-optimizer/0.1 "
    "(+https://github.com/saalocin/football_fantasy_26-27; FPL projection pipeline) httpx"
)

#: Seconds between consecutive requests to one host. There is no published FPL
#: rate limit, so this is a self-imposed figure we can point at if asked.
DEFAULT_MIN_INTERVAL = 1.0

DEFAULT_ATTEMPTS = 5
DEFAULT_BASE_DELAY = 1.0
DEFAULT_MAX_DELAY = 60.0

#: Statuses worth asking again about. ⚠ Every other 4xx is NOT here on purpose:
#: a 404 does not become a 200 because you asked five times, and a 403 asked
#: repeatedly is how a tolerated client stops being tolerated.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class HttpError(Exception):
    """Base for every failure this module raises."""


class NonRetryableStatus(HttpError):
    """The host answered, and the answer will not improve by asking again."""

    def __init__(self, url: str, status: int) -> None:
        super().__init__(f"{status} for {url} — not retryable")
        self.url = url
        self.status = status


class RetriesExhausted(HttpError):
    """Retryable failures all the way to the end of the budget."""

    def __init__(self, url: str, attempts: int, last: str) -> None:
        super().__init__(f"gave up on {url} after {attempts} attempts; last failure: {last}")
        self.url = url
        self.attempts = attempts
        self.last = last


class Throttle:
    """A floor under the gap between requests to one host.

    Not a semaphore and not a token bucket: it does not cap concurrency or
    average rate, it simply refuses to let two requests to the same host happen
    closer together than ``min_interval``. That is the property a politeness
    policy actually needs, and it is the one that is easy to reason about when
    something goes wrong at 3am.

    ``sleep`` and ``monotonic`` are injectable so tests can drive a fake clock
    rather than actually waiting.
    """

    def __init__(
        self,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.min_interval = min_interval
        self._sleep = sleep
        self._monotonic = monotonic
        self._last: dict[str, float] = {}

    def wait(self, host: str) -> None:
        """Block until it is polite to call ``host`` again."""
        last = self._last.get(host)
        if last is not None:
            elapsed = self._monotonic() - last
            if elapsed < self.min_interval:
                self._sleep(self.min_interval - elapsed)
        self._last[host] = self._monotonic()


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Seconds to wait per a ``Retry-After`` header, or ``None`` if unusable.

    The header comes in two shapes — delta-seconds and an HTTP-date — and hosts
    use both. A value we cannot parse returns ``None`` so the caller falls back
    to its own backoff rather than treating garbage as zero and hammering.
    """
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    reference = now or datetime.now(UTC)
    return max(0.0, (when - reference).total_seconds())


def _equal_jitter(ceiling: float, rng: random.Random) -> float:
    """Half the computed delay, plus a random share of the other half.

    Full jitter (uniform over the whole range) can return near-zero and defeat
    the backoff; no jitter at all synchronises retries. Equal jitter keeps the
    growth while still spreading the calls.
    """
    half = ceiling / 2
    return half + rng.random() * half


def fetch(
    url: str,
    *,
    client: httpx.Client,
    throttle: Throttle | None = None,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    headers: dict[str, str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[float], float] | None = None,
) -> bytes:
    """GET ``url`` and return the body as served.

    Retries transport errors and :data:`RETRYABLE_STATUS` with exponential
    backoff plus jitter, honouring ``Retry-After`` when the host sends one.
    Raises :class:`NonRetryableStatus` immediately on any other 4xx.

    Raises:
        NonRetryableStatus: the host answered and will keep answering the same.
        RetriesExhausted: retryable failures for the whole budget.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")

    rng = random.Random()
    pick = jitter or (lambda ceiling: _equal_jitter(ceiling, rng))
    request_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    host = httpx.URL(url).host
    last_failure = "none"

    for attempt in range(1, attempts + 1):
        if throttle is not None:
            throttle.wait(host)

        retry_after: float | None = None
        try:
            response = client.get(url, headers=request_headers)
        except httpx.TransportError as exc:
            last_failure = f"{type(exc).__name__}: {exc}"
        else:
            if response.status_code < 400:
                return response.content
            if response.status_code not in RETRYABLE_STATUS:
                raise NonRetryableStatus(url, response.status_code)
            last_failure = f"HTTP {response.status_code}"
            retry_after = parse_retry_after(response.headers.get("Retry-After"))

        if attempt == attempts:
            break

        # The host's own instruction beats our guess; our guess still caps it,
        # so a hostile or mistaken Retry-After cannot park the process forever.
        ceiling = min(max_delay, base_delay * 2 ** (attempt - 1))
        delay = min(retry_after, max_delay) if retry_after is not None else pick(ceiling)
        sleep(delay)

    raise RetriesExhausted(url, attempts, last_failure)
