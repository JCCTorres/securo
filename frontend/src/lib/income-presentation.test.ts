import { describe, expect, it } from 'vitest'
import { incomeCompositionBreakdowns, savingsRate, drillDownTotal, cashflowTotals, reconcileIncomeAdjustment } from './income-presentation'
import type { IncomeAdjustment, ReportCompositionItem, TransactionsSummary } from '@/types'

const composition: ReportCompositionItem[] = [
  { key: 'salary', label: 'Sample receipts', group: 'income', value: 1000, color: '#000' },
  { key: 'business-deduction', label: 'Business', group: 'business', value: 150, color: '#000' },
  { key: 'food', label: 'Sample spending', group: 'expenses', value: 300, color: '#000' },
  { key: 'investment', label: 'Contributions', group: 'investments', value: 200, color: '#000' },
]
describe('income presentation', () => {
  const booked: IncomeAdjustment = { gross_income: 100, booked_income: 100, projected_income: 0, business_deduction: 200, net_income: -100, currency: 'USD', missing_fx_count: 0, nonbank: true, months: [] }
  it('adds only disclosed forecasts to booked net, preserving negative results and partial FX', () => {
    const card = { ...booked, projected_income: 50, gross_income: 150, net_income: -50, missing_fx_count: 2 }
    expect(reconcileIncomeAdjustment(booked, card)).toEqual(card)
    expect(reconcileIncomeAdjustment(booked)).toEqual(booked)
    expect(reconcileIncomeAdjustment(undefined, card)).toBeUndefined()
    expect(reconcileIncomeAdjustment(booked, { ...card, currency: 'EUR' })).toEqual(booked)
  })
  it('does not invent missing old-server forecast metadata', () => {
    const legacy = { ...booked, booked_income: undefined, projected_income: undefined }
    expect(reconcileIncomeAdjustment(booked, legacy)).toEqual(legacy)
  })
  it('keeps gross composition and business distinct, without duplicate net income', () => {
    expect(incomeCompositionBreakdowns(composition).map(({ key, value }) => [key, value]))
      .toEqual([['grossIncome', 1000], ['businessDeduction', 150], ['expenses', 300], ['investments', 200]])
  })
  it('uses authoritative unpaginated signed totals, never item sums', () => {
    const summary: TransactionsSummary = { income: -50, expense: 300, net: -350, excluded: 0, currency: 'EUR' }
    expect(drillDownTotal(summary, 'credit')).toBe(-50)
    expect(drillDownTotal(summary, 'debit')).toBe(300)
    expect(drillDownTotal(undefined, 'credit')).toBeNull()
  })
  it('does not invent a savings percentage for zero or negative income', () => {
    expect(savingsRate(-50, 300)).toBeNull()
    expect(savingsRate(0, 0)).toBeNull()
    expect(savingsRate(1000, 1200)).toBe(-20)
  })
  it('funds business exactly once and retains true investment contributions', () => {
    expect(cashflowTotals(composition)).toEqual({ gross: 1000, business: 150, expenses: 300, investments: 200, surplus: 350, deficit: 0 })
  })
  it('exposes a funding gap, not fabricated income or investments', () => {
    expect(cashflowTotals(composition.map(c => c.group === 'business' ? { ...c, value: 800 } : c)))
      .toEqual({ gross: 1000, business: 800, expenses: 300, investments: 200, surplus: 0, deficit: 300 })
  })
  it('keeps signed refund reversals in composition data', () => {
    expect(incomeCompositionBreakdowns([{ ...composition[1], value: -50 }])[0].value).toBe(-50)
  })
})
