"""Read-only monthly Business costs. No provider calls, writes or synthetic rows.

Daily deltas are changes in the month-to-date nonnegative deduction. A refund
can therefore produce a negative daily delta, but never a negative month.
Consumers aggregate these deltas, not max(0, all-month debits minus credits).
"""
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.account import Account
from app.models.category import Category
from app.models.fx_rate import FxRate
from app.models.group import Group, GroupMember
from app.models.transaction import Transaction
from app.models.transaction_split import TransactionSplit
from app.schemas.business_income import BusinessMonth, IncomeAdjustment
from app.services._query_filters import reporting_date_col

CENT = Decimal("0.01")
ZERO = Decimal("0")


@dataclass
class BusinessAdjustment:
    currency: str
    months: list[BusinessMonth] = field(default_factory=list)
    daily: dict[date, Decimal] = field(default_factory=dict)

    @property
    def deduction(self) -> float:
        return round(sum(m.deduction for m in self.months), 2)

    def bridge(self, gross_income: float, *, projected_income: float = 0.0, missing_fx_count: int = 0) -> IncomeAdjustment:
        """Explain the consumer's existing gross without changing its projections."""
        gross = round(gross_income, 2)
        projected = round(projected_income, 2)
        return IncomeAdjustment(
            gross_income=gross, booked_income=round(gross - projected, 2),
            projected_income=projected, business_deduction=self.deduction,
            net_income=round(gross - self.deduction, 2), currency=self.currency,
            missing_fx_count=missing_fx_count + sum(m.missing_fx_count for m in self.months), months=self.months,
        )


async def exact_primary_amount(
    session: AsyncSession, tx: Transaction, amount: Decimal, currency: str,
    stored_currency: str | None = None,
) -> Decimal | None:
    """Convert a share using verified exact raw-date target FX.

    Legacy stamps have no durable currency provenance: neither current owner
    preferences nor the compatibility-only stored_currency hint establish it.
    Reuse stored rounding only when both rate and full amount match the cache.
    Same-currency amounts need no FX. No latest-rate/network fallback.
    """
    if tx.currency == currency:
        return amount.quantize(CENT, rounding=ROUND_HALF_UP)

    rates = (await session.execute(select(FxRate).where(FxRate.date == tx.date))).scalars()
    pairs = {(r.base_currency, r.quote_currency): r.rate for r in rates if r.rate > 0}
    rate = pairs.get((tx.currency, currency))
    if rate is None and (currency, tx.currency) in pairs:
        rate = Decimal(1) / pairs[(currency, tx.currency)]
    if rate is None:
        source = Decimal(1) if tx.currency == "USD" else pairs.get(("USD", tx.currency))
        target = Decimal(1) if currency == "USD" else pairs.get(("USD", currency))
        if source and target:
            rate = target / source
    if rate is None:
        return None
    if (tx.amount and tx.amount_primary is not None and tx.fx_rate_used == rate
            and abs(tx.amount_primary) == (abs(tx.amount) * rate).quantize(CENT, rounding=ROUND_HALF_EVEN)):
        # Match the stamp producer's full-row rounding before prorating shares.
        return (abs(tx.amount_primary) * amount / abs(tx.amount)).quantize(CENT, rounding=ROUND_HALF_UP)
    return (amount * rate).quantize(CENT, rounding=ROUND_HALF_UP)


async def get_business_adjustment(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID,
    start: date | None, end: date | None, currency: str,
    accounting_mode: str = "cash", account_ids: list[uuid.UUID] | None = None,
    category_ids: list[uuid.UUID] | None = None, *,
    include_shared: bool = True, exclude_closed: bool = True,
    transaction_ids=None, date_column=None, stored_currency: str | None = None,
) -> BusinessAdjustment:
    """End is exclusive. Account filters intentionally use raw account amounts.

    Unfiltered personal views deduct only the owner's share and include invited
    viewer shares, matching the existing Splitwise projection. A selected-ID
    subquery can constrain arbitrary transaction-list/bill filters without
    accidentally applying an unrelated workspace-wide deduction.
    """
    result = BusinessAdjustment(currency)
    if account_ids == [] or category_ids == []:
        return result
    report_date = date_column if date_column is not None else reporting_date_col(accounting_mode)
    invited_ids = select(GroupMember.id).where(
        GroupMember.linked_user_id == user_id, GroupMember.is_self.is_(False),
    )
    shared_ids = select(TransactionSplit.transaction_id).where(
        TransactionSplit.group_member_id.in_(invited_ids),
    )
    scope = Transaction.workspace_id == workspace_id
    if include_shared and account_ids is None:
        scope = or_(scope, and_(Transaction.user_id != user_id,
                               Transaction.id.in_(shared_ids)))
    conditions = [scope, Category.is_business.is_(True),
                  Transaction.is_ignored.is_(False), Transaction.status == "posted",
                  Transaction.transfer_pair_id.is_(None),
                  Transaction.source.not_in(["opening_balance", "settlement"])]
    if exclude_closed:
        conditions.append(Account.is_closed.is_(False))
    if start is not None:
        conditions.append(report_date >= start)
    if end is not None:
        conditions.append(report_date < end)
    if account_ids is not None:
        conditions.append(Transaction.account_id.in_(account_ids))
    if category_ids is not None:
        conditions.append(Transaction.category_id.in_(category_ids))
    if transaction_ids is not None:
        conditions.append(Transaction.id.in_(transaction_ids))
    rows = (await session.execute(
        select(Transaction, report_date).join(Category, Transaction.category_id == Category.id)
        .join(Account, Transaction.account_id == Account.id)
        .where(*conditions).options(selectinload(Transaction.splits))
        .order_by(report_date, Transaction.id)
    )).all()
    if not rows:
        return result
    own_ids = set((await session.scalars(
        select(GroupMember.id).outerjoin(Group, Group.id == GroupMember.group_id).where(
            or_(GroupMember.linked_user_id == user_id,
                and_(GroupMember.is_self.is_(True), Group.user_id == user_id)),
        )
    )).all())
    invited = set((await session.scalars(invited_ids)).all())
    daily: dict[date, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    missing: dict[str, int] = defaultdict(int)
    for tx, day in rows:
        amount = abs(tx.amount)
        if account_ids is None:
            if tx.workspace_id != workspace_id:
                amount = sum((s.share_amount for s in tx.splits
                              if s.group_member_id in invited), ZERO)
            elif tx.user_id == user_id and tx.splits:
                amount -= sum((s.share_amount for s in tx.splits
                               if s.group_member_id not in own_ids), ZERO)
        if amount <= 0:
            continue
        converted = await exact_primary_amount(session, tx, amount, currency, stored_currency)
        month = day.strftime("%Y-%m")
        if converted is None:
            missing[month] += 1
            daily[day]  # retain a month with only missing conversion(s)
            continue
        daily[day][0 if tx.type == "debit" else 1] += converted
    months: dict[str, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    for day, (debit, credit) in sorted(daily.items()):
        month = day.strftime("%Y-%m")
        totals = months[month]
        before = max(ZERO, totals[0] - totals[1])
        totals[0] += debit
        totals[1] += credit
        result.daily[day] = max(ZERO, totals[0] - totals[1]) - before
    result.months = [BusinessMonth(
        month=month, debits=float(debit), credits=float(credit),
        deduction=float(max(ZERO, debit - credit)), missing_fx_count=missing[month],
    ) for month, (debit, credit) in sorted(months.items())]
    return result
