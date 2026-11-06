"""Provider HTTP boundary; direct callers need no proxy or storage dependencies."""

from contextlib import contextmanager
from contextvars import ContextVar
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests


_budget = ContextVar("weather_request_budget", default=None)


class RateLimited(ValueError):
    def __init__(self, retry_after=60):
        super().__init__("Upstream weather request rate limited")
        self.retry_after = retry_after


@contextmanager
def request_budget(guard):
    token = _budget.set(guard)
    try:
        yield
    finally:
        _budget.reset(token)


def request(method, *args, **kwargs):
    guard = _budget.get()
    if guard:
        guard()
    response = getattr(requests, method)(*args, **kwargs)
    if response.status_code == 429:
        retry = response.headers.get("Retry-After", "60")
        try:
            seconds = max(1, float(retry))
        except (ValueError, TypeError):
            try:
                seconds = max(1, (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError):
                seconds = 60
        response.close()
        if not math.isfinite(seconds):
            seconds = 60
        raise RateLimited(seconds)
    return response


def get(*args, **kwargs):
    return request("get", *args, **kwargs)


def post(*args, **kwargs):
    return request("post", *args, **kwargs)
