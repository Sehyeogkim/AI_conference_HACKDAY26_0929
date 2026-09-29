"""Errors raised by the World Labs client. Messages never contain the API key."""

from __future__ import annotations

from typing import Literal

WorldLabsErrorKind = Literal[
    "invalid_request",  # HTTP 400: generic rejection, often an unreachable input URL or a policy violation
    "auth",  # HTTP 401 or 403
    "insufficient_credits",  # HTTP 402
    "not_found",  # HTTP 404 (also "not an API-enabled user" on the credits endpoint)
    "validation",  # HTTP 422: the request did not match the API schema
    "rate_limited",  # HTTP 429
    "server",  # HTTP 5xx
    "unexpected_status",  # any other HTTP status
    "redirect_refused",  # a redirect that could leak the key or leave the allowed hosts
    "host_not_allowed",  # a URL outside the allowed hosts
    "transport",  # network failure or timeout
    "invalid_response",  # a response body that does not match the documented shape
    "generation_failed",  # an operation finished with an error
    "timeout",  # waiting for an operation took longer than allowed
    "download_failed",  # an asset download failed its integrity checks
]

HTTP_STATUS_TO_ERROR_KIND: dict[int, WorldLabsErrorKind] = {
    400: "invalid_request",
    401: "auth",
    402: "insufficient_credits",
    403: "auth",
    404: "not_found",
    422: "validation",
    429: "rate_limited",
}


def error_kind_for_status(status: int) -> WorldLabsErrorKind:
    if status in HTTP_STATUS_TO_ERROR_KIND:
        return HTTP_STATUS_TO_ERROR_KIND[status]
    if 500 <= status <= 599:
        return "server"
    return "unexpected_status"


class WorldLabsError(RuntimeError):
    """A failed World Labs call. `status` is the HTTP status when there was one."""

    def __init__(
        self,
        kind: WorldLabsErrorKind,
        message: str,
        *,
        status: int | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(f"[{kind}] {message}")
        self.kind = kind
        self.status = status
        self.retry_after_s = retry_after_s
        self.detail = message


class BudgetRefusedError(RuntimeError):
    """The budget guard refused a paid call before anything was sent."""

    def __init__(self, reason: str, *, estimated_credits: int) -> None:
        super().__init__(f"World Labs budget guard refused the request: {reason}")
        self.reason = reason
        self.estimated_credits = estimated_credits
