"""One realized personal-row projection for income reconciliation consumers.

Bank/account selections retain full amounts; unfiltered personal selections use
owner/invited shares. Convert each share on its transaction date before summing.
Missing exact conversion remains explicit rather than becoming foreign 1:1.
"""
from decimal import Decimal

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import selectinload

from app.models.account import Account
from app.models.group import Group, GroupMember
from app.models.transaction import Transaction
from app.models.transaction_split import TransactionSplit
from app.services._query_filters import counts_as_user_pnl, reporting_date_col
from app.services.business_income_service import exact_primary_amount


async def converted_personal_rows(session, rows, workspace_id, user_id, currency, *, raw=False):
    own = set((await session.scalars(select(GroupMember.id).outerjoin(Group).where(or_(
        GroupMember.linked_user_id == user_id,
        and_(GroupMember.is_self.is_(True), Group.user_id == user_id),
    )))).all())
    invited = set((await session.scalars(select(GroupMember.id).where(
        GroupMember.linked_user_id == user_id, GroupMember.is_self.is_(False),
    ))).all())
    result = []
    for tx, day in rows:
        amount = abs(tx.amount)
        if not raw:
            if tx.workspace_id != workspace_id:
                amount = sum((s.share_amount for s in tx.splits if s.group_member_id in invited), Decimal(0))
            elif tx.user_id == user_id:
                amount -= sum((s.share_amount for s in tx.splits if s.group_member_id not in own), Decimal(0))
        if amount <= 0:
            continue
        converted = await exact_primary_amount(session, tx, amount, currency)
        result.append((tx, day, converted))
    return result


async def get_personal_pnl_rows(session, workspace_id, user_id, currency,
                                start=None, end=None, accounting_mode="cash", account_ids=None):
    report_date = reporting_date_col(accounting_mode)
    scope = Transaction.workspace_id == workspace_id
    if account_ids is None:
        invited = select(GroupMember.id).where(
            GroupMember.linked_user_id == user_id, GroupMember.is_self.is_(False))
        shared = select(TransactionSplit.transaction_id).where(TransactionSplit.group_member_id.in_(invited))
        scope = or_(scope, and_(Transaction.user_id != user_id, Transaction.id.in_(shared)))
    conditions = [scope, Account.is_closed.is_(False), Transaction.status == "posted",
                  Transaction.source != "opening_balance", counts_as_user_pnl()]
    if start is not None:
        conditions.append(report_date >= start)
    if end is not None:
        conditions.append(report_date < end)
    if account_ids is not None:
        conditions.append(Transaction.account_id.in_(account_ids))
    rows = (await session.execute(select(Transaction, report_date)
        .join(Account, Transaction.account_id == Account.id).where(*conditions)
        .options(selectinload(Transaction.splits), selectinload(Transaction.category)))).all()
    return await converted_personal_rows(session, rows, workspace_id, user_id, currency,
                                         raw=account_ids is not None)
