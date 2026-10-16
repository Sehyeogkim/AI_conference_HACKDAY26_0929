"""Optional private-demo access gate, shared by HTTP and WebSocket routes.

Set COFFEE_ACCESS_TOKEN to a high-entropy secret before starting the server.
Open /?access_token=<secret> once to establish an HttpOnly session cookie;
the redirect removes the token from the address bar. Disable URL access logs
on a public deployment, since the first request necessarily carries the token.
With the environment variable unset, local HTTP and WebSocket access is unchanged.
"""
from __future__ import annotations

import hmac
import os

from fastapi import FastAPI, Request, WebSocket
from starlette.responses import PlainTextResponse, RedirectResponse


COOKIE_NAME = "coffee_demo_access"
HEADER_NAME = "X-Coffee-Token"
UNAUTHORIZED_MESSAGE = (
    "This is a private Coffee Lab demo. Open the complete private demo link "
    "provided by the host in this browser, then try again. "
    "If you do not have the link, ask the demo host for access."
)


def _token() -> str:
    return os.environ.get("COFFEE_ACCESS_TOKEN", "")


def _matches(candidate: str | None, expected: str) -> bool:
    """Compare arbitrary user input without leaking the expected token."""
    return candidate is not None and hmac.compare_digest(
        candidate.encode("utf-8"), expected.encode("utf-8")
    )


def _authorized(connection: Request | WebSocket, expected: str) -> bool:
    # Evaluate both comparisons so matching a cookie does not short-circuit
    # validation of a supplied header. Neither secret is logged or returned.
    cookie_matches = _matches(connection.cookies.get(COOKIE_NAME), expected)
    header_matches = _matches(connection.headers.get(HEADER_NAME), expected)
    return cookie_matches or header_matches


def authorized_websocket(ws: WebSocket) -> bool:
    """Return authorization before accepting a WebSocket; query tokens are HTTP-only.

    The caller should reject an unauthorized socket before ws.accept(). Browsers
    authenticate their socket using the HttpOnly cookie from the initial GET.
    """
    expected = _token()
    return not expected or _authorized(ws, expected)


def install_access_gate(app: FastAPI) -> None:
    """Gate every HTTP path when COFFEE_ACCESS_TOKEN is configured.

    Only a GET of / can exchange an access_token query argument for a cookie.
    Headers remain available for scripts, health checks, and authorized clients.
    Token values are read per request so HTTP and WebSocket checks agree.
    """
    if getattr(app.state, "coffee_access_gate_installed", False):
        return
    app.state.coffee_access_gate_installed = True

    @app.middleware("http")
    async def require_demo_access(request: Request, call_next):
        expected = _token()
        if not expected:
            return await call_next(request)

        if request.method == "GET" and request.url.path == "/":
            supplied = request.query_params.get("access_token")
            if _matches(supplied, expected):
                response = RedirectResponse("/", status_code=303)
                forwarded_proto = request.headers.get("x-forwarded-proto", "")
                secure = request.url.scheme == "https" or (
                    forwarded_proto.split(",", 1)[0].strip().lower() == "https"
                )
                response.set_cookie(
                    COOKIE_NAME,
                    expected,
                    httponly=True,
                    secure=secure,
                    samesite="strict",
                    path="/",
                )
                response.headers["Cache-Control"] = "no-store"
                response.headers["Referrer-Policy"] = "no-referrer"
                return response

        if not _authorized(request, expected):
            return PlainTextResponse(
                UNAUTHORIZED_MESSAGE,
                status_code=401,
                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
            )
        return await call_next(request)
