import type { IncomeAdjustment, ReportBreakdown, ReportCompositionItem, TransactionsSummary } from '@/types'

export const BUSINESS_COLOR = '#8B5CF6'

/** The caller supplies a card only for the exact same date/account scope.
 * Real totals always come from the unpaginated transaction summary. */
export function reconcileIncomeAdjustment(booked?: IncomeAdjustment | null, card?: IncomeAdjustment | null): IncomeAdjustment | undefined {
  if (!booked) return undefined
  if (!card || card.currency !== booked.currency) return booked
  // Older servers do not disclose the split; never invent a forecast amount.
  if (card.projected_income == null) return card
  return {
    ...booked,
    booked_income: booked.booked_income ?? booked.gross_income,
    projected_income: card.projected_income,
    gross_income: booked.gross_income + card.projected_income,
    net_income: booked.net_income + card.projected_income,
    missing_fx_count: Math.max(booked.missing_fx_count, card.missing_fx_count),
  }
}

/** A nonpositive income denominator cannot describe a meaningful savings rate. */
export function savingsRate(income: number, expenses: number): number | null {
  return income > 0 ? ((income - expenses) / income) * 100 : null
}

/** Totals come from the full scoped query, never the visible page of bank rows. */
export function drillDownTotal(summary: TransactionsSummary | undefined, type?: 'credit' | 'debit'): number | null {
  if (!summary) return null
  return type === 'credit' ? summary.income : type === 'debit' ? summary.expense : summary.net
}

/** Composition is gross receipts, not net income plus another deduction. */
export function incomeCompositionBreakdowns(items: ReportCompositionItem[]): ReportBreakdown[] {
  const groups = [
    { group: 'income', key: 'grossIncome', label: 'Gross income', color: '#10B981' },
    { group: 'business', key: 'businessDeduction', label: 'Business', color: BUSINESS_COLOR },
    { group: 'expenses', key: 'expenses', label: 'Personal expenses', color: '#F43F5E' },
    { group: 'investments', key: 'investments', label: 'Investment contributions', color: '#0EA5E9' },
  ]
  return groups.filter(g => items.some(c => c.group === g.group)).map(({ group, ...rest }) => ({
    ...rest, value: items.filter(c => c.group === group).reduce((sum, c) => sum + c.value, 0),
  }))
}

export function cashflowTotals(items: ReportCompositionItem[]) {
  const sum = (group: string) => items.filter(c => c.group === group).reduce((s, c) => s + c.value, 0)
  const gross = sum('income')
  const business = sum('business')
  const expenses = sum('expenses')
  const investments = sum('investments')
  const remaining = gross - business - expenses - investments
  return { gross, business, expenses, investments, surplus: Math.max(0, remaining), deficit: Math.max(0, -remaining) }
}
