"""Shared gain budgets: two sleeves cannot spend the same allowance."""
from datetime import datetime, timedelta, timezone


def _expiry(minutes=30):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def test_second_sleeve_cannot_consume_a_reserved_budget(store, tenant):
    ok_first, _ = store.reserve(tenant, "tu_patel", "2026", "9000.00", "run_a",
                                "15000.00", _expiry())
    ok_second, message = store.reserve(tenant, "tu_patel", "2026", "9000.00", "run_b",
                                       "15000.00", _expiry())
    assert ok_first is True
    assert ok_second is False
    assert "already reserved" in message


def test_release_returns_capacity(store, tenant):
    _, reservation_id = store.reserve(tenant, "tu_patel", "2026", "15000.00", "run_a",
                                      "15000.00", _expiry())
    store.release(tenant, reservation_id)
    ok, _ = store.reserve(tenant, "tu_patel", "2026", "15000.00", "run_b",
                          "15000.00", _expiry())
    assert ok is True


def test_expired_reservation_frees_capacity(store, tenant):
    store.reserve(tenant, "tu_patel", "2026", "15000.00", "run_a", "15000.00",
                  _expiry(minutes=-5))
    ok, _ = store.reserve(tenant, "tu_patel", "2026", "15000.00", "run_b",
                          "15000.00", _expiry())
    assert ok is True


def test_budgets_are_keyed_by_tax_unit_not_household(store, tenant):
    # A household can contain more than one taxpayer; a trust's allowance is
    # not the joint account's allowance.
    store.reserve(tenant, "tu_patel", "2026", "15000.00", "run_a", "15000.00", _expiry())
    ok, _ = store.reserve(tenant, "tu_patel_trust", "2026", "15000.00", "run_b",
                          "15000.00", _expiry())
    assert ok is True


def test_periods_are_independent(store, tenant):
    store.reserve(tenant, "tu_patel", "2026", "15000.00", "run_a", "15000.00", _expiry())
    ok, _ = store.reserve(tenant, "tu_patel", "2027", "15000.00", "run_b",
                          "15000.00", _expiry())
    assert ok is True
