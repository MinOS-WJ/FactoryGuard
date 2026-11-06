"""Shared helpers for outbound HTTP calls.

Every network integration in FactoryGuard talks to a customer device or a
customer service over plain HTTP.  The helpers here keep the call sites
short and, more importantly, make sure failed responses are always closed
so the application never leaks sockets during long running sessions.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Any


def read_http_error(exc: urllib.error.HTTPError, *, limit: int = 2048) -> str:
    """Read an HTTP error body and always close the response.

    ``urllib`` hands back a file-like object for error responses.  Leaving
    it open keeps the socket alive until the garbage collector runs, which
    shows up as a ``ResourceWarning`` in long sessions.
    """
    try:
        return exc.read(limit).decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return ""
    finally:
        try:
            exc.close()
        except (OSError, ValueError):
            pass


def http_status(response: Any) -> int:
    """Return the HTTP status code of a response object."""
    try:
        return int(getattr(response, "status", 200))
    except (TypeError, ValueError):
        return 200


def close_http_error(exc: BaseException) -> None:
    """Release the socket behind an HTTP error response.

    Callers that only need the status code still have to close the error
    object, otherwise ``urllib`` keeps the connection open.
    """
    if isinstance(exc, urllib.error.HTTPError):
        try:
            exc.close()
        except (OSError, ValueError):
            pass


__all__ = ["close_http_error", "http_status", "read_http_error"]
