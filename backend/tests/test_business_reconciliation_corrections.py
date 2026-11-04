"""Synthetic cross-consumer discriminators for realized personal income."""
from datetime import date
from decimal import Decimal

import pytest

from app.models import Account, Category, FxRate, Group, GroupMember, TransactionSplit, User, Workspace
from app.services import dashboard_service, report_service, transaction_service
from tests.test_business_income_adjustment import postgres_dates_on_sqlite as sqlite_dates, seed  # noqa: F401

pytestmark = pytest.mark.usefixtures("sqlite_dates")


async def report(session, workspace, user, **kwargs):
    return await report_service.get_income_expenses_report(
        session, workspace.id, user.id, months=1, **kwargs)


def assert_components(actual, gross):
    assert actual.meta.income_adjustment.gross_income == gross
    assert sum(c.value for c in actual.composition if c.group == "income") == gross
    assert sum(c.total for c in actual.category_trend if c.group == "income") == gross
    assert sum(c.value for p in actual.trend for c in p.composition if c.group == "income") == gross


async def test_r1_foreign_mixed_receipts_same_scope(
    session, test_user, test_workspace, test_account, client, auth_headers,
):
    test_user.preferences = {"currency_display": "USD"}
    today = date.today()
    cat = Category(user_id=test_user.id, name="Synthetic cost", is_business=True)
    session.add_all([cat, FxRate(base_currency="USD", quote_currency="EUR", date=today,
                                rate=Decimal("0.8"), source="synthetic")])
    await session.commit()
    await seed(session, test_user, test_account, date=today, type="credit", currency="EUR",
               amount=Decimal("100"))
    await seed(session, test_user, test_account, date=today, category_id=cat.id, amount=Decimal("20"))
    for extra in (False, True):
        if extra:
            # A separate pair preserves the original 15-unit stored receipt
            # without asserting conflicting EUR/USD rates on the same date.
            session.add(FxRate(base_currency="GBP", quote_currency="USD", date=today,
                               rate=Decimal("1.5"), source="synthetic"))
            await seed(session, test_user, test_account, date=today, type="credit", currency="GBP",
                       amount=Decimal("10"), amount_primary=Decimal("15"), fx_rate_used=Decimal("1.5"))
        expected = 140 if extra else 125
        dash = await dashboard_service.get_summary(session, test_workspace.id, test_user.id)
        response = await client.get("/api/transactions", headers=auth_headers, params={
            "from": today.replace(day=1).isoformat(), "to": today.isoformat(),
            "type": "credit", "user_pnl_only": "true", "limit": 1})
        assert response.status_code == 200
        assert dash.income_adjustment is not None
        assert dash.income_adjustment.gross_income == expected
        assert response.json()["summary"]["income_adjustment"]["gross_income"] == expected
        actual = await report(session, test_workspace, test_user)
        assert_components(actual, expected)
        assert actual.meta.income_adjustment.net_income == expected - 20


@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("invited", [False, True])
async def test_r2_income_components_share_and_raw_scope(
    session, test_user, test_workspace, test_account, invited, pending,
):
    test_user.preferences = {"currency_display": "USD"}
    user, workspace, account = test_user, test_workspace, test_account
    if invited:
        user = User(email="synthetic-other@example.test", hashed_password="unused")
        session.add(user)
        await session.flush()
        workspace = Workspace(name="Synthetic foreign", kind="personal", created_by_user_id=user.id)
        session.add(workspace)
        await session.flush()
        account = Account(user_id=user.id, workspace_id=workspace.id, name="Synthetic",
                          type="checking", currency="USD")
        session.add(account)
    group = Group(user_id=user.id, workspace_id=workspace.id, name="Synthetic", default_currency="USD")
    cost = Category(user_id=test_user.id, name="Synthetic Business", is_business=True)
    inv = Category(user_id=test_user.id, name="Synthetic investment", treat_as_transfer=True)
    session.add_all([group, cost, inv])
    await session.flush()
    own = GroupMember(group_id=group.id, name="Owner", is_self=True)
    other = GroupMember(group_id=group.id, name="Other", is_self=False,
                        linked_user_id=test_user.id if invited else None)
    session.add_all([own, other])
    await session.commit()
    tx = await seed(session, user, account, date=date.today(), type="credit", amount=Decimal("1000"),
                    status="pending" if pending else "posted")
    session.add_all([
        TransactionSplit(transaction_id=tx.id, group_member_id=own.id,
                         share_amount=Decimal("600" if invited else "400"), share_type="exact"),
        TransactionSplit(transaction_id=tx.id, group_member_id=other.id,
                         share_amount=Decimal("400" if invited else "600"), share_type="exact"),
    ])
    await session.commit()
    await seed(session, test_user, test_account, date=date.today(), category_id=cost.id)
    await seed(session, test_user, test_account, date=date.today(), category_id=inv.id, amount=Decimal("75"))
    actual = await report(session, test_workspace, test_user)
    assert_components(actual, 0 if pending else 400)
    assert actual.meta.income_adjustment.net_income == (-200 if pending else 200)
    assert sum(c.value for c in actual.composition if c.group == "investments") == 75
    scoped = await report(session, test_workspace, test_user, account_ids=[test_account.id])
    assert_components(scoped, 0 if invited or pending else 1000)


async def test_r3_pending_not_realized_with_splits_and_forecasts(
    session, test_user, test_workspace, test_account, monkeypatch,
):
    cat = Category(user_id=test_user.id, name="Synthetic Business", is_business=True)
    group = Group(user_id=test_user.id, workspace_id=test_workspace.id, name="Synthetic", default_currency="USD")
    session.add_all([cat, group])
    await session.flush()
    other = GroupMember(group_id=group.id, name="Other", is_self=False)
    session.add(other)
    await session.commit()
    for kind in ("credit", "debit"):
        tx = await seed(session, test_user, test_account, date=date.today(), type=kind,
                        status="pending", amount=Decimal("1000"))
        session.add(TransactionSplit(transaction_id=tx.id, group_member_id=other.id,
                                     share_amount=Decimal("600"), share_type="exact"))
        await session.commit()
    await seed(session, test_user, test_account, date=date.today(), category_id=cat.id, status="pending")
    await seed(session, test_user, test_account, date=date.today(), category_id=cat.id)
    await seed(session, test_user, test_account, date=date.today(), type="credit", amount=Decimal("50"))
    async def projections(_session, _workspace, start, end, *_args, **_kwargs):
        return ([{"category_id": None, "amount": 100, "type": "credit", "currency": test_user.primary_currency}]
                if start <= date.today() < end else [])
    monkeypatch.setattr(dashboard_service, "_get_recurring_projections", projections)
    actual = await report(session, test_workspace, test_user)
    assert_components(actual, 150)
    bridge = actual.meta.income_adjustment
    assert (bridge.booked_income, bridge.projected_income, bridge.business_deduction) == (50, 100, 200)
    assert sum(p.breakdowns["expenses"] for p in actual.trend) == 0
    trend = await dashboard_service.get_monthly_trend(session, test_workspace.id, test_user.id)
    assert trend[-1].income_adjustment is not None
    assert trend[-1].income_adjustment.booked_income == 50
    assert trend[-1].expenses == 0


@pytest.mark.parametrize("currency_change", [False, True])
@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("kind", ["credit", "debit"])
async def test_q1_invited_stamp_currency_provenance(
    session, test_user, test_workspace, cached, kind, currency_change,
):
    from sqlalchemy import select

    from app.api.workspaces import update_workspace
    from app.models import Transaction
    from app.models.workspace import WorkspaceMember
    from app.schemas.workspace import WorkspaceUpdate

    async def bank_rows():
        return [(await session.execute(select(model.__table__).order_by(model.id))).all()
                for model in (Account, Transaction, TransactionSplit)]
    test_user.preferences = {"currency_display": "USD"}
    owner = User(email="synthetic-owner@example.test", hashed_password="unused",
                 preferences={"currency_display": "BRL"})
    session.add(owner)
    await session.flush()
    workspace = Workspace(name="Synthetic foreign", kind="personal", created_by_user_id=owner.id)
    session.add(workspace)
    await session.flush()
    account = Account(user_id=owner.id, workspace_id=workspace.id, name="Synthetic", type="checking", currency="EUR")
    group = Group(user_id=owner.id, workspace_id=workspace.id, name="Synthetic", default_currency="EUR")
    cat = Category(user_id=owner.id, workspace_id=workspace.id, name="Synthetic", is_business=kind == "debit")
    session.add_all([account, group, cat])
    await session.flush()
    member = GroupMember(group_id=group.id, name="Invited", is_self=False, linked_user_id=test_user.id)
    session.add(member)
    await session.commit()
    tx = await seed(session, owner, account, date=date.today(), type=kind, currency="EUR", category_id=cat.id,
                    amount=Decimal("100"), amount_primary=Decimal("500"), fx_rate_used=Decimal("5"))
    session.add(TransactionSplit(transaction_id=tx.id, group_member_id=member.id,
                                 share_amount=Decimal("20"), share_type="exact"))
    if cached:
        session.add(FxRate(base_currency="EUR", quote_currency="USD", date=date.today(),
                           rate=Decimal("1.25"), source="synthetic"))
    await session.commit()
    before = await bank_rows()
    if currency_change:
        session.add(WorkspaceMember(workspace_id=workspace.id, user_id=owner.id, role="owner"))
        await session.commit()
        changed = await update_workspace(workspace.id, WorkspaceUpdate(default_currency="USD"), session, owner)
        assert changed.default_currency == owner.primary_currency == "USD"
        assert await bank_rows() == before
    _, total, summary = await transaction_service.get_transactions(
        session, test_workspace.id, test_user.id, txn_type="credit", user_pnl_only=True, include_summary=True)
    assert summary is not None
    bridge = summary["income_adjustment"]
    expected = 25 if cached else 0
    assert bridge.gross_income == (expected if kind == "credit" else 0)
    assert bridge.business_deduction == (expected if kind == "debit" else 0)
    assert bridge.missing_fx_count == (0 if cached else 1)
    assert total == (1 if kind == "credit" else 0)
    actual = await report(session, test_workspace, test_user)
    assert actual.meta.income_adjustment == bridge
    dash = await dashboard_service.get_summary(session, test_workspace.id, test_user.id)
    assert dash.income_adjustment == bridge
    assert_components(actual, expected if kind == "credit" else 0)
    trend = await dashboard_service.get_monthly_trend(session, test_workspace.id, test_user.id)
    assert trend[-1].income_adjustment == bridge
    assert await bank_rows() == before


@pytest.mark.parametrize("stored_amount,stored_rate,cached,expected", [
    ("0.04", "1.5", True, "0.01"),  # prorate the verified rounded full stamp
    ("0.09", "1.5", True, "0.02"),  # rate alone cannot authenticate the amount
    ("0.04", "3", True, "0.02"),  # full amount alone cannot authenticate the rate
    ("0.04", "1.5", False, None),
])
async def test_q1b_verified_full_stamp_share_rounding(
    session, test_user, test_account, stored_amount, stored_rate, cached, expected,
):
    from app.services.business_income_service import exact_primary_amount

    test_user.preferences = {"currency_display": "USD"}
    tx = await seed(session, test_user, test_account, currency="EUR", amount=Decimal("0.03"),
                    amount_primary=Decimal(stored_amount), fx_rate_used=Decimal(stored_rate))
    # Current preferences cannot establish historical stamp currency. Only the
    # exact raw-date target cache corroborates both rate and producer rounding.
    if cached:
        session.add(FxRate(base_currency="EUR", quote_currency="USD", date=tx.date,
                           rate=Decimal("1.5"), source="synthetic"))
        await session.commit()
    actual = await exact_primary_amount(session, tx, Decimal("0.01"), "USD", "USD")
    assert actual == (Decimal(expected) if expected is not None else None)
    assert await exact_primary_amount(session, tx, Decimal("0.01"), "EUR") == Decimal("0.01")
