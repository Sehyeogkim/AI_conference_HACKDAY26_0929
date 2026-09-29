import pytest

from agriphilo.credits import Ledger
from agriphilo.stripe_checkout import confirmed_topup, retrieve_session


def paid_session(**changes):
    session = {
        "id": "cs_test_123", "livemode": False, "mode": "payment",
        "status": "complete", "payment_status": "paid", "currency": "usd",
        "client_reference_id": "requester", "amount_total": 10000,
        "metadata": {"kind": "credit_topup", "customer": "requester", "credits": "100"},
    }
    session.update(changes)
    return session


def test_only_paid_matching_test_checkout_can_credit_wallet(tmp_path):
    ledger = Ledger(tmp_path / "credits.json")
    session = paid_session()
    credits = confirmed_topup(session, "requester")
    ledger.topup("requester", credits, f"stripe:{session['id']}")
    ledger.topup("requester", credits, f"stripe:{session['id']}")
    assert ledger.balance("wallet:requester") == 100
    assert len(ledger.entries) == 1

    for rejected in (
        paid_session(payment_status="unpaid"),
        paid_session(livemode=True),
        paid_session(client_reference_id="somebody-else"),
        paid_session(amount_total=100),
        paid_session(metadata={"kind": "credit_topup", "customer": "requester", "credits": "NaN"}),
    ):
        with pytest.raises(ValueError):
            confirmed_topup(rejected, "requester")


def test_retrieve_session_rejects_non_test_id_before_api_call():
    with pytest.raises(ValueError):
        retrieve_session("cs_live_123")
    with pytest.raises(ValueError):
        retrieve_session("cs_test_123/line_items")
