"""Stripe Checkout (test mode) for paying an order's quote.

Uses the Stripe REST API directly (no SDK). The secret key comes from STRIPE_SECRET_KEY (or STRIPE_API_KEY) in .env and must be a
test key (sk_test_...): live keys are refused, so this can never make a real charge. The browser is sent to
Stripe's hosted checkout page; on return the server retrieves the session and only then marks the order paid.
"""
import os
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict

import httpx

from .brainbase import load_dotenv

API = "https://api.stripe.com/v1"


def _key() -> str:
    load_dotenv()
    return (os.environ.get("STRIPE_SECRET_KEY") or os.environ.get("STRIPE_API_KEY") or "").strip()


def enabled() -> bool:
    return _key().startswith("sk_test_")


def _client() -> httpx.Client:
    key = _key()
    if not key.startswith("sk_test_"):
        raise RuntimeError("Stripe is not configured: set a test secret key (sk_test_...) as STRIPE_SECRET_KEY in .env")
    return httpx.Client(base_url=API, auth=(key, ""), timeout=30)


def create_session(order_id: str, amount_usd: float, description: str, success_url: str, cancel_url: str) -> Dict[str, Any]:
    """Create a one-line Checkout Session for the quote total. Returns the session (id, url, ...)."""
    cents = int(round(amount_usd * 100))
    with _client() as c:
        r = c.post("/checkout/sessions", data={
            "mode": "payment",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": order_id,
            "metadata[order_id]": order_id,
            "line_items[0][quantity]": 1,
            "line_items[0][price_data][currency]": "usd",
            "line_items[0][price_data][unit_amount]": cents,
            "line_items[0][price_data][product_data][name]": f"WEMINE dataset order {order_id[:8]}",
            "line_items[0][price_data][product_data][description]": description[:500],
        })
    if r.status_code >= 400:
        raise RuntimeError(f"Stripe: {r.json().get('error', {}).get('message', r.text)}")
    return r.json()


def retrieve_session(session_id: str) -> Dict[str, Any]:
    if not re.fullmatch(r"cs_test_[A-Za-z0-9]+", session_id):
        raise ValueError("invalid test Checkout Session ID")
    with _client() as c:
        r = c.get(f"/checkout/sessions/{session_id}")
    if r.status_code >= 400:
        raise RuntimeError(f"Stripe: {r.json().get('error', {}).get('message', r.text)}")
    return r.json()


def confirmed_topup(session: Dict[str, Any], customer: str) -> float:
    """Return credits only after checking Stripe's test payment and our wallet contract."""
    metadata = session.get("metadata") or {}
    if (not str(session.get("id", "")).startswith("cs_test_") or session.get("livemode") is not False
            or session.get("mode") != "payment" or session.get("status") != "complete"
            or session.get("payment_status") != "paid" or session.get("currency") != "usd"
            or session.get("client_reference_id") != customer
            or metadata.get("kind") != "credit_topup" or metadata.get("customer") != customer):
        raise ValueError("Stripe has not confirmed a paid test top-up for this wallet")
    try:
        credits = Decimal(str(metadata["credits"]))
    except (KeyError, TypeError, InvalidOperation) as exc:
        raise ValueError("invalid credit amount in Stripe session") from exc
    cents = credits * 100
    if (not credits.is_finite() or not 1 <= credits <= 100000
            or cents != cents.to_integral_value()
            or session.get("amount_total") != int(cents)):
        raise ValueError("paid amount does not match the credits")
    return float(credits)


def create_topup_session(customer: str, credits: float, success_url: str, cancel_url: str) -> Dict[str, Any]:
    """Checkout Session that buys wallet credits (1 credit = 1 USD)."""
    cents = int(round(credits * 100))
    with _client() as c:
        r = c.post("/checkout/sessions", data={
            "mode": "payment",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": customer,
            "metadata[kind]": "credit_topup",
            "metadata[customer]": customer,
            "metadata[credits]": f"{credits:g}",
            "line_items[0][quantity]": 1,
            "line_items[0][price_data][currency]": "usd",
            "line_items[0][price_data][unit_amount]": cents,
            "line_items[0][price_data][product_data][name]": f"WEMINE credits × {credits:,.0f}",
            "line_items[0][price_data][product_data][description]": "Prepaid credits for WEMINE robot data orders (1 credit = 1 USD)",
        })
    if r.status_code >= 400:
        raise RuntimeError(f"Stripe: {r.json().get('error', {}).get('message', r.text)}")
    return r.json()
