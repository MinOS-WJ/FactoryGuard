from __future__ import annotations

import io
import urllib.error
from types import SimpleNamespace

from factoryguard.net import close_http_error, http_status, read_http_error


def test_read_http_error_returns_body_and_closes_response():
    closed: list[bool] = []

    class TrackedError(urllib.error.HTTPError):
        def close(self) -> None:
            closed.append(True)
            super().close()

    error = TrackedError(
        "https://example.test",
        500,
        "server error",
        {},
        io.BytesIO("服务器错误".encode()),
    )

    detail = read_http_error(error)

    assert detail == "服务器错误"
    assert closed == [True]


def test_read_http_error_survives_unreadable_body():
    class BrokenError(urllib.error.HTTPError):
        def read(self, *_args):
            raise OSError("closed")

    error = BrokenError("https://example.test", 500, "server error", {}, io.BytesIO(b""))

    assert read_http_error(error) == ""


def test_close_http_error_only_touches_http_errors():
    error = urllib.error.HTTPError("https://example.test", 500, "server error", {}, io.BytesIO(b""))

    close_http_error(error)
    close_http_error(ValueError("not an http error"))


def test_http_status_defaults_to_two_hundred():
    assert http_status(SimpleNamespace(status=204)) == 204
    assert http_status(SimpleNamespace(status="bad")) == 200
    assert http_status(SimpleNamespace()) == 200
