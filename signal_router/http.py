from __future__ import annotations

import random
import time
from typing import Any, Protocol

import requests


class Transport(Protocol):
    """Anything with a requests-style .request(). Tests pass a fake."""

    def request(self, method: str, url: str, **kwargs: Any) -> Any: ...


class HttpError(Exception):
    def __init__(self, status: int, body: str, url: str):
        super().__init__(f"HTTP {status} from {url}: {body[:300]}")
        self.status = status
        self.body = body


def call(
    transport: Transport,
    method: str,
    url: str,
    *,
    max_attempts: int = 5,
    base_delay: float = 0.5,
    sleep=time.sleep,
    **kwargs: Any,
) -> Any:
    """One HTTP call with retries on 429 and 5xx.

    Honors Retry-After when the API sends it, otherwise exponential backoff with
    jitter so a burst of retries doesn't hit the API in lockstep. 4xx other than
    429 fail immediately: retrying a bad request just wastes rate limit.
    Returns parsed JSON (or None for empty bodies).
    """
    kwargs.setdefault("timeout", 20)
    for attempt in range(1, max_attempts + 1):
        resp = transport.request(method, url, **kwargs)
        status = resp.status_code
        if status < 400:
            if not resp.content:
                return None
            return resp.json()
        retryable = status == 429 or status >= 500
        if not retryable or attempt == max_attempts:
            raise HttpError(status, resp.text, url)
        retry_after = resp.headers.get("Retry-After")
        if retry_after is not None:
            try:
                delay = float(retry_after)
            except ValueError:
                delay = base_delay * 2 ** (attempt - 1)
        else:
            delay = base_delay * 2 ** (attempt - 1)
        sleep(delay + random.uniform(0, base_delay))
    raise AssertionError("unreachable")


def default_transport() -> Transport:
    return requests.Session()
