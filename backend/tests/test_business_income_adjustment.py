"""Synthetic native-service regressions for the Business read model."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.category import Category
from app.models.transaction import Transaction
from app.schemas.category import CategoryCreate, CategoryRead, CategoryUpdate
from app.services import category_service, dashboard_service


async def test_category_flag_round_trip_and_scope(session, test_user, test_workspace):
    cat = await category_service.create_category(
        session, test_workspace.id, test_user.id,
        CategoryCreate(name="Synthetic business", is_business=True),
    )
    assert CategoryRead.model_validate(cat).is_business is True
    assert await category_service.update_category(
        session, cat.id, uuid.uuid4(), CategoryUpdate(is_business=False)
    ) is None
    updated = await category_service.update_category(
        session, cat.id, test_workspace.id, CategoryUpdate(is_business=False)
    )
    assert CategoryRead.model_validate(updated).is_business is False
    ordinary = await category_service.create_category(
        session, test_workspace.id, test_user.id, CategoryCreate(name="Business")
    )
    assert ordinary.is_business is False


async def seed(session, user, account, **overrides):
    values = dict(user_id=user.id, account_id=account.id,
                  workspace_id=account.workspace_id, description="Synthetic fixture",
                  amount=Decimal("200"), currency=user.primary_currency,
                  date=date(2025, 6, 10), type="debit", source="manual")
    values.update(overrides)
    tx = Transaction(**values)
    session.add(tx)
    await session.commit()
    return tx


async def test_native_dashboard_reversible_no_bank_writes(
    session, test_user, test_workspace, test_account,
):
    cat = Category(user_id=test_user.id, name="Synthetic business", is_business=True)
    session.add(cat)
    await session.commit()
    # Mixed primary coverage must not hide the unstamped cost/refund on reversal.
    await seed(session, test_user, test_account, amount=Decimal("1000"),
               amount_primary=Decimal("1000"), type="credit")
    await seed(session, test_user, test_account, category_id=cat.id)
    async def summary():
        return await dashboard_service.get_summary(
            session, test_workspace.id, test_user.id, month=date(2025, 6, 1)
        )
    actual = await summary()
    assert actual.monthly_income_primary == 800
    assert actual.monthly_expenses_primary == 0
    assert actual.income_adjustment.gross_income == 1000
    assert actual.income_adjustment.business_deduction == 200
    balance = actual.total_balance
    await seed(session, test_user, test_account, amount=Decimal("50"),
               category_id=cat.id, type="credit")
    actual = await summary()
    assert actual.monthly_income_primary == 850
    assert actual.income_adjustment.business_deduction == 150
    bank_balance = actual.total_balance
    before = (await session.execute(select(Transaction.id, Transaction.amount,
              Transaction.category_id, Transaction.source))).all()
    cat.is_business = False
    await session.commit()
    actual = await summary()
    assert actual.monthly_income_primary == 1050
    assert actual.monthly_expenses_primary == 200
    assert actual.total_balance == bank_balance
    cat.is_business = True
    await session.commit()
    actual = await summary()
    assert actual.monthly_income_primary == 850
    after = (await session.execute(select(Transaction.id, Transaction.amount,
             Transaction.category_id, Transaction.source))).all()
    assert before == after
    assert await session.scalar(select(func.count()).select_from(Transaction)) == 3
    assert balance == bank_balance  # connected balance remains provider-owned


@pytest.mark.parametrize("overrides", [
    {"status": "pending"}, {"is_ignored": True}, {"source": "opening_balance"},
    {"source": "settlement"}, {"transfer_pair_id": uuid.UUID(int=1)},
])
async def test_ineligible_business_never_deducts(
    session, test_user, test_workspace, test_account, overrides,
):
    cat = Category(user_id=test_user.id, name="Synthetic business", is_business=True)
    session.add(cat)
    await session.commit()
    await seed(session, test_user, test_account, category_id=cat.id, **overrides)
    actual = await dashboard_service.get_summary(
        session, test_workspace.id, test_user.id, month=date(2025, 6, 1)
    )
    assert actual.income_adjustment is not None
    assert actual.income_adjustment.business_deduction == 0
    assert actual.monthly_expenses_primary == 0


async def test_ignored_is_not_business_but_explicit_legacy_business_is(
    session, test_user, test_workspace, test_account,
):
    ordinary = Category(user_id=test_user.id, name="Business", is_ignored=True)
    business = Category(user_id=test_user.id, name="Synthetic", is_ignored=True,
                        treat_as_transfer=True, is_business=True)
    session.add_all([ordinary, business])
    await session.commit()
    await seed(session, test_user, test_account, category_id=ordinary.id)
    await seed(session, test_user, test_account, category_id=business.id)
    actual = await dashboard_service.get_summary(
        session, test_workspace.id, test_user.id, month=date(2025, 6, 1)
    )
    assert actual.monthly_income_primary == -200
    assert actual.income_adjustment is not None
    assert actual.income_adjustment.business_deduction == 200
    assert actual.monthly_expenses_primary == 0


async def test_monthly_floor_fx_rounding_and_missing_warning(
    session, test_user, test_workspace, test_account,
):
    from app.models.fx_rate import FxRate
    from app.services.business_income_service import get_business_adjustment
    test_user.preferences = {"currency_display": "USD"}
    cat = Category(user_id=test_user.id, name="Synthetic", is_business=True)
    session.add(cat)
    # Current owner preferences do not prove historical stamp currency.
    # Corroborate the two original stored-rate fixtures on their raw dates.
    session.add_all([
        FxRate(base_currency="EUR", quote_currency="USD", date=date(2025, 6, 10),
               rate=Decimal("1.25"), source="synthetic"),
        FxRate(base_currency="EUR", quote_currency="USD", date=date(2025, 6, 11),
               rate=Decimal("1.5"), source="synthetic"),
    ])
    await session.commit()
    await seed(session, test_user, test_account, category_id=cat.id,
               currency="EUR", amount=Decimal("100"), amount_primary=Decimal("125"),
               fx_rate_used=Decimal("1.25"))
    await seed(session, test_user, test_account, category_id=cat.id,
               currency="EUR", amount=Decimal("100"), amount_primary=Decimal("150"),
               fx_rate_used=Decimal("1.5"), date=date(2025, 6, 11))
    session.add(FxRate(base_currency="USD", quote_currency="EUR", date=date(2025, 6, 12),
                       rate=Decimal("0.8"), source="test"))
    await seed(session, test_user, test_account, category_id=cat.id,
               currency="EUR", amount=Decimal("0.03"), date=date(2025, 6, 12))
    # An adjacent cached date and a legacy fallback stamp must not become 1:1.
    await seed(session, test_user, test_account, category_id=cat.id,
               currency="EUR", amount=Decimal("100"), amount_primary=Decimal("100"),
               fx_rate_used=Decimal("1"), date=date(2025, 6, 13))
    await seed(session, test_user, test_account, category_id=cat.id,
               amount=Decimal("500"), type="credit", date=date(2025, 7, 10))
    actual = await get_business_adjustment(session, test_workspace.id, test_user.id,
        date(2025, 6, 1), date(2025, 8, 1), "USD")
    assert actual.deduction == 275.04
    assert [m.deduction for m in actual.months] == [275.04, 0]
    assert actual.bridge(100).net_income == -175.04
    assert actual.bridge(100).missing_fx_count == 1
    assert sum(actual.daily.values()) == Decimal("275.04")


@pytest.mark.parametrize("mode,override,expected", [
    ("cash", None, 200), ("accrual", None, 0),
    ("cash", date(2025, 7, 20), 0), ("accrual", date(2025, 6, 20), 200),
])
async def test_reporting_date_and_account_workspace_scope(
    session, test_user, test_workspace, test_account, mode, override, expected,
):
    from app.services.business_income_service import get_business_adjustment
    cat = Category(user_id=test_user.id, name="Synthetic", is_business=True)
    session.add(cat)
    await session.commit()
    await seed(session, test_user, test_account, category_id=cat.id,
               effective_date=date(2025, 7, 10), effective_bill_date=override)
    async def calculate(workspace, accounts=None):
        return await get_business_adjustment(session, workspace, test_user.id,
            date(2025, 6, 1), date(2025, 7, 1), test_user.primary_currency, mode, accounts)
    assert (await calculate(test_workspace.id)).deduction == expected
    assert (await calculate(uuid.uuid4())).deduction == 0
    assert (await calculate(test_workspace.id, [])).deduction == 0
    assert (await calculate(test_workspace.id, [uuid.uuid4()])).deduction == 0
    assert (await calculate(test_workspace.id, [test_account.id])).deduction == expected


async def test_owner_share_and_filtered_raw_account(
    session, test_user, test_workspace, test_account,
):
    from app.models.group import Group, GroupMember
    from app.models.transaction_split import TransactionSplit
    from app.services.business_income_service import get_business_adjustment
    group = Group(user_id=test_user.id, workspace_id=test_workspace.id,
                  name="Synthetic group", default_currency=test_user.primary_currency)
    cat = Category(user_id=test_user.id, name="Synthetic", is_business=True)
    session.add_all([group, cat])
    await session.flush()
    own = GroupMember(group_id=group.id, name="Owner", is_self=True)
    other = GroupMember(group_id=group.id, name="Other", is_self=False)
    session.add_all([own, other])
    await session.commit()
    tx = await seed(session, test_user, test_account, category_id=cat.id)
    session.add_all([
        TransactionSplit(transaction_id=tx.id, group_member_id=own.id,
                         share_amount=Decimal("80"), share_type="exact"),
        TransactionSplit(transaction_id=tx.id, group_member_id=other.id,
                         share_amount=Decimal("120"), share_type="exact"),
    ])
    await session.commit()
    actual = await dashboard_service.get_summary(session, test_workspace.id, test_user.id,
                                                 month=date(2025, 6, 1))
    assert actual.monthly_income_primary == -80
    assert actual.monthly_expenses_primary == 0
    raw = await get_business_adjustment(session, test_workspace.id, test_user.id,
        date(2025, 6, 1), date(2025, 7, 1), test_user.primary_currency,
        account_ids=[test_account.id])
    assert raw.deduction == 200


async def test_transaction_pagination_bridge_budget_and_account_bank_totals(
    session, test_user, test_workspace, test_account,
):
    from app.services import account_service, budget_service, transaction_service
    test_account.currency = test_user.primary_currency
    cat = Category(user_id=test_user.id, name="Synthetic", is_business=True)
    session.add(cat)
    await session.commit()
    await seed(session, test_user, test_account, amount=Decimal("700"), type="credit")
    await seed(session, test_user, test_account, amount=Decimal("300"), type="credit")
    await seed(session, test_user, test_account, category_id=cat.id)
    items, count, summary = await transaction_service.get_transactions(
        session, test_workspace.id, test_user.id, from_date=date(2025, 6, 1),
        to_date=date(2025, 6, 30), txn_type="credit", user_pnl_only=True,
        include_summary=True, limit=1,
    )
    assert len(items) == 1 and count == 2
    assert summary is not None
    assert summary["income"] == 800
    assert summary["income_adjustment"].gross_income == 1000
    _, _, filtered = await transaction_service.get_transactions(
        session, test_workspace.id, test_user.id, txn_type="credit", user_pnl_only=True,
        include_summary=True, category_ids=[uuid.uuid4()],
    )
    assert filtered is not None
    assert filtered["income"] == 0
    assert filtered["income_adjustment"].business_deduction == 0
    budget = await budget_service.get_budget_vs_actual(
        session, test_workspace.id, test_user.id, month=date(2025, 6, 1))
    assert not budget
    account = await account_service.get_account_summary(session, test_account.id, test_workspace.id,
        date_from=date(2025, 6, 1), date_to=date(2025, 6, 30))
    assert account is not None
    assert account["income_adjustment"].net_income == 800
    assert account["personal_expenses"] == 0
    assert account["monthly_expenses"] == 200  # bank cash/bill facts unchanged


@pytest.fixture
async def postgres_dates_on_sqlite(session, monkeypatch):
    """Only emulate PostgreSQL date formatting; execute all real query logic."""
    connection = await session.connection()
    def register(sync_connection):
        def to_char(value, pattern):
            day = date.fromisoformat(str(value)[:10])
            formats = {"YYYY-MM": "%Y-%m", "YYYY-MM-DD": "%Y-%m-%d", "YYYY": "%Y",
                       'IYYY-"W"IW': "%G-W%V"}
            return day.strftime(formats[pattern])
        sync_connection.connection.create_function("to_char", 2, to_char)
    await connection.run_sync(register)
    from app.services import report_service
    original = report_service._interval_label_expr
    def label(interval, date_col=None):
        if interval == "weekly":
            return func.to_char(date_col, 'IYYY-"W"IW')
        return original(interval, date_col)
    monkeypatch.setattr(report_service, "_interval_label_expr", label)


@pytest.mark.parametrize("interval", ["daily", "weekly", "monthly", "yearly"])
async def test_complete_report_business_bridge_not_investment(
    session, test_user, test_workspace, test_account, postgres_dates_on_sqlite, interval,
):
    from app.services import report_service
    today = date.today()
    business = Category(user_id=test_user.id, name="Synthetic business",
                        is_business=True, treat_as_transfer=True)
    investment = Category(user_id=test_user.id, name="Synthetic investment", treat_as_transfer=True)
    session.add_all([business, investment])
    await session.commit()
    await seed(session, test_user, test_account, date=today, type="credit", amount=Decimal("1000"))
    await seed(session, test_user, test_account, date=today, category_id=business.id)
    await seed(session, test_user, test_account, date=today, category_id=business.id,
               type="credit", amount=Decimal("50"))
    await seed(session, test_user, test_account, date=today, category_id=investment.id,
               amount=Decimal("75"))
    actual = await report_service.get_income_expenses_report(
        session, test_workspace.id, test_user.id, months=1, interval=interval)
    assert actual.meta.income_adjustment is not None
    assert actual.meta.income_adjustment.net_income == 850
    assert actual.summary.primary_value == 850
    assert sum(p.breakdowns["income"] for p in actual.trend) == 850
    assert sum(p.breakdowns["businessDeduction"] for p in actual.trend) == 150
    assert sum(c.value for c in actual.composition if c.group == "income") == 1000
    assert sum(c.value for c in actual.composition if c.group == "business") == 150
    assert sum(c.value for c in actual.composition if c.group == "investments") == 75
    assert sum(c.value for c in actual.composition if c.group == "expenses") == 0
    assert [c.total for c in actual.category_trend if c.group == "business"] == [150]
    trend = await dashboard_service.get_monthly_trend(session, test_workspace.id, test_user.id)
    assert trend[-1].income == 850


async def test_report_discloses_projected_income_separately(
    session, test_user, test_workspace, test_account, postgres_dates_on_sqlite, monkeypatch,
):
    from app.services import report_service

    await seed(session, test_user, test_account, date=date.today(),
               type="credit", amount=Decimal("1000"))
    async def projections(_session, _workspace, start, end, *_args, **_kwargs):
        if start <= date.today() < end:
            return [{"category_id": None, "amount": 100, "type": "credit",
                     "currency": test_user.primary_currency}]
        return []
    monkeypatch.setattr(dashboard_service, "_get_recurring_projections", projections)
    actual = await report_service.get_income_expenses_report(
        session, test_workspace.id, test_user.id, months=1, interval="monthly")
    bridge = actual.meta.income_adjustment
    assert bridge is not None
    assert bridge.gross_income == 1100
    assert bridge.booked_income == 1000
    assert bridge.projected_income == 100
    assert sum(p.breakdowns["bookedIncome"] for p in actual.trend) == 1000
    assert sum(p.breakdowns["projectedIncome"] for p in actual.trend) == 100


async def test_category_api_roundtrip(client, auth_headers):
    created = await client.post("/api/categories", headers=auth_headers,
                                json={"name": "Synthetic", "is_business": True})
    assert created.status_code == 201
    assert created.json()["is_business"] is True
    category_id = created.json()["id"]
    changed = await client.patch(f"/api/categories/{category_id}", headers=auth_headers,
                                 json={"is_business": False})
    assert changed.status_code == 200
    assert changed.json()["is_business"] is False
    listed = await client.get("/api/categories", headers=auth_headers)
    assert next(c for c in listed.json() if c["id"] == category_id)["is_business"] is False
    rejected = await client.patch(f"/api/categories/{category_id}", headers=auth_headers,
                                  json={"is_business": None})
    assert rejected.status_code == 422


@pytest.mark.parametrize("business_enabled", [False, True])
async def test_dashboard_booked_plus_recurring_projection_reconciles_to_real_drilldown(
    session, test_user, test_workspace, test_account, client, auth_headers,
    business_enabled,
):
    from app.models.recurring_transaction import RecurringTransaction

    category = Category(user_id=test_user.id, name="Synthetic cost",
                        is_business=business_enabled)
    session.add(category)
    await session.commit()
    await seed(session, test_user, test_account, amount=Decimal("1000"),
               amount_primary=Decimal("1000"), type="credit")
    await seed(session, test_user, test_account, category_id=category.id,
               amount_primary=Decimal("200"))
    session.add(RecurringTransaction(
        user_id=test_user.id, workspace_id=test_workspace.id, account_id=test_account.id,
        description="Synthetic projected income", amount=Decimal("300"),
        currency=test_user.primary_currency, type="credit", frequency="monthly",
        start_date=date(2025, 6, 20), next_occurrence=date(2025, 6, 20),
        auto_generate=False,
    ))
    await session.commit()
    before = (await session.execute(select(Transaction.id, Transaction.amount))).all()
    dashboard = await dashboard_service.get_summary(
        session, test_workspace.id, test_user.id, month=date(2025, 6, 1),
    )
    response = await client.get("/api/transactions", headers=auth_headers, params={
        "from": "2025-06-01", "to": "2025-06-30", "type": "credit",
        "user_pnl_only": "true", "limit": 1,
    })
    assert response.status_code == 200
    drilldown = response.json()
    bridge = dashboard.income_adjustment
    assert bridge is not None
    assert bridge.booked_income == 1000
    assert bridge.projected_income == 300
    assert bridge.gross_income == bridge.booked_income + bridge.projected_income == 1300
    assert bridge.business_deduction == (200 if business_enabled else 0)
    assert dashboard.monthly_income_primary == bridge.net_income == (
        bridge.gross_income - bridge.business_deduction
    )
    assert dashboard.monthly_expenses_primary == (0 if business_enabled else 200)
    real = drilldown["summary"]["income_adjustment"]
    assert real["booked_income"] == real["gross_income"] == bridge.booked_income
    assert real["projected_income"] == 0
    assert real["net_income"] + bridge.projected_income == bridge.net_income
    assert drilldown["total"] == len(drilldown["items"]) == 1
    assert (await session.execute(select(Transaction.id, Transaction.amount))).all() == before
    filtered = await dashboard_service.get_summary(
        session, test_workspace.id, test_user.id, month=date(2025, 6, 1), account_ids=[],
    )
    assert filtered.income_adjustment is not None
    assert filtered.income_adjustment.booked_income == 0
    assert filtered.income_adjustment.projected_income == 0


@pytest.mark.parametrize("business_enabled", [False, True])
async def test_personal_summary_uses_unpaginated_owner_shares_and_keeps_bank_scope(
    session, test_user, test_workspace, test_account, client, auth_headers,
    business_enabled,
):
    from app.models.group import Group, GroupMember
    from app.models.transaction_split import TransactionSplit

    group = Group(user_id=test_user.id, workspace_id=test_workspace.id,
                  name="Synthetic income group", default_currency=test_user.primary_currency)
    cost = Category(user_id=test_user.id, name="Synthetic cost", is_business=business_enabled)
    receipts = Category(user_id=test_user.id, name="Synthetic receipts")
    session.add_all([group, cost, receipts])
    await session.flush()
    owner = GroupMember(group_id=group.id, name="Owner", is_self=True)
    other = GroupMember(group_id=group.id, name="Other", is_self=False)
    session.add_all([owner, other])
    await session.commit()
    split = await seed(session, test_user, test_account, type="credit",
                       amount=Decimal("1000"), amount_primary=Decimal("1000"),
                       category_id=receipts.id, description="Synthetic matched receipt")
    await seed(session, test_user, test_account, type="credit", amount=Decimal("100"),
               amount_primary=Decimal("100"), description="Synthetic other receipt")
    await seed(session, test_user, test_account, category_id=cost.id,
               amount_primary=Decimal("200"))
    session.add_all([
        TransactionSplit(transaction_id=split.id, group_member_id=owner.id,
                         share_amount=Decimal("400"), share_type="exact"),
        TransactionSplit(transaction_id=split.id, group_member_id=other.id,
                         share_amount=Decimal("600"), share_type="exact"),
    ])
    await session.commit()
    before = (await session.execute(select(Transaction.id, Transaction.amount))).all()

    async def fetch(**filters):
        params = {"from": "2025-06-01", "to": "2025-06-30", "type": "credit",
                  "user_pnl_only": "true", "limit": 1, **filters}
        response = await client.get("/api/transactions", headers=auth_headers, params=params)
        assert response.status_code == 200
        return response.json()

    first, second = await fetch(), await fetch(page=2)
    assert first["total"] == second["total"] == 2
    rows = first["items"] + second["items"]
    row_total = sum(Decimal(str(r["viewer_share"] if r["viewer_share"] is not None
                                else r["amount"])) for r in rows)
    bridge = first["summary"]["income_adjustment"]
    assert bridge["gross_income"] == row_total == 500
    assert bridge["booked_income"] == 500 and bridge["projected_income"] == 0
    assert first["summary"] == second["summary"]
    assert bridge["net_income"] == (300 if business_enabled else 500)
    dashboard = await dashboard_service.get_summary(
        session, test_workspace.id, test_user.id, month=date(2025, 6, 1),
    )
    assert dashboard.income_adjustment is not None
    assert dashboard.income_adjustment.gross_income == bridge["gross_income"]
    assert dashboard.monthly_income_primary == bridge["net_income"]
    for filters in ({"q": "matched"}, {"category_id": str(receipts.id)}):
        scoped = await fetch(**filters)
        assert scoped["total"] == 1
        assert scoped["summary"]["income_adjustment"]["gross_income"] == 400
        assert scoped["summary"]["income_adjustment"]["business_deduction"] == 0
    for filters in ({"q": "absent"}, {"account_id": str(uuid.uuid4())},
                    {"from": "2025-07-01", "to": "2025-07-31"}):
        scoped = await fetch(**filters)
        assert scoped["total"] == 0
        assert scoped["summary"]["income_adjustment"]["gross_income"] == 0
    # Account/collection and default bank-list summaries retain full amounts.
    for filters in ({"account_id": str(test_account.id)}, {"user_pnl_only": "false"}):
        bank = await fetch(**filters)
        assert bank["summary"]["income_adjustment"]["gross_income"] == 1100
    assert (await session.execute(select(Transaction.id, Transaction.amount))).all() == before


async def test_personal_summary_invited_share_stored_fx_and_workspace_isolation(
    session, test_user, test_workspace, test_account,
):
    from app.models.account import Account
    from app.models.group import Group, GroupMember
    from app.models.transaction_split import TransactionSplit
    from app.models.user import User
    from app.models.workspace import Workspace
    from app.services.transaction_service import get_transactions

    from app.models.fx_rate import FxRate

    # Preferences cannot prove stamp-time currency; exact-date cache below
    # corroborates the original stored amounts without changing the shares.
    other = User(email="synthetic-owner@example.test", hashed_password="unused",
                 preferences={"currency_display": test_user.primary_currency})
    session.add(other)
    await session.flush()
    foreign_workspace = Workspace(name="Synthetic foreign", kind="personal",
                                  created_by_user_id=other.id)
    session.add(foreign_workspace)
    await session.flush()
    foreign_account = Account(user_id=other.id, workspace_id=foreign_workspace.id,
                              name="Synthetic account", type="checking", currency="EUR")
    group = Group(user_id=other.id, workspace_id=foreign_workspace.id,
                  name="Synthetic shared income", default_currency="EUR")
    session.add_all([foreign_account, group])
    await session.flush()
    owner = GroupMember(group_id=group.id, name="Owner", is_self=True)
    invited = GroupMember(group_id=group.id, name="Invited", is_self=False,
                          linked_user_id=test_user.id)
    session.add_all([owner, invited])
    await session.commit()
    # No visibility unless invited, even with matching search and date.
    await seed(session, other, foreign_account, type="credit", amount=Decimal("9000"))
    for day in (10, 11):
        session.add(FxRate(base_currency="EUR", quote_currency=test_user.primary_currency,
                           date=date(2025, 5, day), rate=Decimal("1.3333333333"), source="synthetic"))
        tx = await seed(session, other, foreign_account, type="credit", currency="EUR",
                        amount=Decimal("3"), amount_primary=Decimal("4"),
                        fx_rate_used=Decimal("1.3333333333"), date=date(2025, 5, day),
                        effective_bill_date=date(2025, 6, day))
        session.add_all([
            TransactionSplit(transaction_id=tx.id, group_member_id=owner.id,
                             share_amount=Decimal("2"), share_type="exact"),
            TransactionSplit(transaction_id=tx.id, group_member_id=invited.id,
                             share_amount=Decimal("1"), share_type="exact"),
        ])
        await session.commit()
    rows, total, summary = await get_transactions(
        session, test_workspace.id, test_user.id, user_pnl_only=True, txn_type="credit",
        include_summary=True, from_date=date(2025, 6, 1), to_date=date(2025, 6, 30),
        limit=1,
    )
    assert total == 2 and len(rows) == 1
    assert rows[0].is_shared and rows[0].viewer_share == 1
    assert rows[0].amount == 3 and rows[0].amount_primary == 4  # never mutate bank data
    assert summary is not None
    assert summary["income_adjustment"].gross_income == 2.66  # round each real share
    assert summary["income_adjustment"].booked_income == 2.66
    assert summary["income_adjustment"].projected_income == 0
    _, total, scoped = await get_transactions(
        session, test_workspace.id, test_user.id, user_pnl_only=True, txn_type="credit",
        include_summary=True, account_ids=[test_account.id],
    )
    assert total == 0 and scoped is not None
    assert scoped["income_adjustment"].gross_income == 0


def test_migration_upgrade_downgrade_and_existing_default():
    import importlib.util
    from pathlib import Path
    from unittest.mock import patch
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, text
    versions = Path(__file__).resolve().parents[1] / "alembic"
    assert ScriptDirectory(str(versions)).get_current_head() == "066"
    spec = importlib.util.spec_from_file_location("business_migration", versions / "versions/066_category_business.py")
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE categories (id INTEGER PRIMARY KEY, name TEXT)"))
        connection.execute(text("INSERT INTO categories VALUES (1, 'Business')"))
        with patch.object(migration, "op", Operations(MigrationContext.configure(connection))):
            migration.upgrade()
            assert connection.execute(text("SELECT is_business FROM categories")).scalar_one() == 0
            migration.downgrade()
            assert [r[1] for r in connection.execute(text("PRAGMA table_info(categories)"))] == ["id", "name"]
    engine.dispose()
