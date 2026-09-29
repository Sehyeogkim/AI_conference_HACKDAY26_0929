"""A minimal HTTP transport that never follows redirects on its own.

The client decides what to do with a redirect (usually: refuse it), so a key header can never be
carried to a different host by the HTTP library. Tests replace this transport with a fake one.
"""

from __future__ import annotations

import shutil
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

DEFAULT_TIMEOUT_S = 60.0
DOWNLOAD_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None
    timeout_s: float = DEFAULT_TIMEOUT_S

    def __repr__(self) -> str:
        # Header values are never shown: one of them may be the API key.
        return f"HttpRequest(method={self.method!r}, url={url_for_messages(self.url)!r}, header_names={sorted(self.headers)!r})"


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def header(self, name: str) -> str | None:
        lowered_name = name.lower()
        for header_name, header_value in self.headers.items():
            if header_name.lower() == lowered_name:
                return header_value
        return None


@dataclass(frozen=True)
class StreamedDownload:
    """Result of streaming a response body to a file. For non-2xx statuses nothing is written."""

    status: int
    headers: dict[str, str]
    bytes_written: int

    def header(self, name: str) -> str | None:
        return HttpResponse(self.status, self.headers, b"").header(name)


class HttpTransportError(RuntimeError):
    """Network-level failure (connection, TLS, timeout). The message never includes headers."""


class HttpTransport(Protocol):
    def send(self, request: HttpRequest) -> HttpResponse: ...

    def stream_to_file(self, request: HttpRequest, destination: Path) -> StreamedDownload: ...


def url_for_messages(url: str) -> str:
    """The URL without its query string: signed asset URLs carry credentials there."""
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Make urllib surface 3xx responses to the caller instead of following them."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - urllib hook
        return None


class UrllibHttpTransport:
    """Standard-library transport. Redirects are returned as ordinary 3xx responses."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(_RefuseRedirects())

    def _open(self, request: HttpRequest):
        urllib_request = urllib.request.Request(
            request.url, data=request.body, headers=dict(request.headers), method=request.method
        )
        try:
            return self._opener.open(urllib_request, timeout=request.timeout_s)
        except urllib.error.HTTPError as http_error:
            return http_error
        except (urllib.error.URLError, TimeoutError, OSError) as network_error:
            reason = getattr(network_error, "reason", network_error)
            raise HttpTransportError(
                f"{request.method} {url_for_messages(request.url)} failed: {type(network_error).__name__}: {reason}"
            ) from None

    def send(self, request: HttpRequest) -> HttpResponse:
        response = self._open(request)
        with response:
            try:
                body = response.read()
            except (TimeoutError, OSError) as read_error:
                raise HttpTransportError(f"reading {url_for_messages(request.url)} failed: {type(read_error).__name__}") from None
            return HttpResponse(status=response.status, headers=dict(response.headers.items()), body=body)

    def stream_to_file(self, request: HttpRequest, destination: Path) -> StreamedDownload:
        response = self._open(request)
        with response:
            headers = dict(response.headers.items())
            if not 200 <= response.status <= 299:
                return StreamedDownload(status=response.status, headers=headers, bytes_written=0)
            destination.parent.mkdir(parents=True, exist_ok=True)
            partial_path = destination.with_name(destination.name + ".partial")
            try:
                with partial_path.open("wb") as partial_file:
                    shutil.copyfileobj(response, partial_file, DOWNLOAD_CHUNK_BYTES)
            except (TimeoutError, OSError) as read_error:
                partial_path.unlink(missing_ok=True)
                raise HttpTransportError(f"downloading {url_for_messages(request.url)} failed: {type(read_error).__name__}") from None
            partial_path.replace(destination)
            return StreamedDownload(status=response.status, headers=headers, bytes_written=destination.stat().st_size)
