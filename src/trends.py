"""Google Trends access with polite pacing and retries.

Google answers bursts of requests with HTTP 429 ("too many requests"). The
scheduled job used to fail on those weeks; now it waits and retries, and if
Google still refuses, it raises `TrendsUnavailable` so the caller can keep
the last good data instead of publishing a broken refresh.
"""
from __future__ import annotations

import sys
import time
import warnings
from typing import Callable, List

import pandas as pd

from .radar import tidy_interest


class TrendsUnavailable(RuntimeError):
    """Google Trends kept refusing requests after every retry."""


def with_retries(fn: Callable[[], pd.DataFrame], attempts: int = 4, base_wait: float = 20.0,
                 sleep: Callable[[float], None] = time.sleep) -> pd.DataFrame:
    """Call `fn`, backing off 20s, 40s, 80s... when Google rate-limits us."""
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # pytrends raises its own 429 type plus requests errors
            rate_limited = "429" in str(exc) or "TooManyRequests" in type(exc).__name__
            if not rate_limited or i == attempts - 1:
                raise TrendsUnavailable(str(exc)) from exc
            wait = base_wait * (2 ** i)
            print(f"[WAIT] Google Trends rate limit; retrying in {wait:.0f}s", file=sys.stderr)
            sleep(wait)
    raise TrendsUnavailable("no attempts made")


class TrendsClient:
    def __init__(self, timeframe: str = "today 5-y", pause: float = 3.0):
        from pytrends.request import TrendReq  # imported lazily so tests don't need network
        self._req = TrendReq
        self.timeframe = timeframe
        self.pause = pause
        self._clients = {}

    def _client(self, hl: str):
        if hl not in self._clients:
            self._clients[hl] = self._req(hl=hl, tz=0)
        return self._clients[hl]

    def interest(self, terms: List[str], geo: str, hl: str = "en-US") -> pd.DataFrame:
        """Weekly interest for up to 5 terms in one request (so they share one scale)."""
        client = self._client(hl)

        def call():
            client.build_payload(terms, cat=0, timeframe=self.timeframe, geo=geo, gprop="")
            with warnings.catch_warnings():  # pytrends' own pandas FutureWarning
                warnings.simplefilter("ignore", FutureWarning)
                return client.interest_over_time()

        raw = with_retries(call)
        time.sleep(self.pause)
        return tidy_interest(raw)
