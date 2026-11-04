import { renderToStaticMarkup } from 'react-dom/server'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { TransactionDrillDown, type DrillDownFilter } from './transaction-drill-down'
import { transactions } from '@/lib/api'
import en from '@/locales/en.json'
import type { IncomeAdjustment, PaginatedTransactions, Transaction } from '@/types'

type Query = { queryKey: unknown[]; queryFn: () => Promise<unknown>; enabled?: boolean }
const harness = vi.hoisted(() => ({ queries: [] as Query[], data: undefined as PaginatedTransactions | undefined }))
vi.mock('@tanstack/react-query', () => ({ useQuery: (q: Query) => {
  harness.queries.push(q)
  return { data: q.queryKey[0] === 'drill-down' ? harness.data : q.queryKey[0] === 'admin' ? { mode: 'cash' } : [], isLoading: false }
} }))
vi.mock('@/lib/api', () => ({ transactions: { list: vi.fn(async () => ({})) }, dashboard: { projectedTransactions: vi.fn(async () => []) }, admin: { accountingMode: vi.fn() } }))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }) }))
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US' }))
vi.mock('@/hooks/use-privacy-mode', () => ({ usePrivacyMode: () => ({ mask: (v: string) => v }) }))
const i18n = createInstance()
void i18n.init({ lng: 'en', resources: { en: { translation: en } }, initImmediate: false })
const booked: IncomeAdjustment = { booked_income: 1000, projected_income: 0, gross_income: 1000, business_deduction: 150, net_income: 850, currency: 'USD', missing_fx_count: 0, months: [], nonbank: true }
const card: IncomeAdjustment = { ...booked, projected_income: 300, gross_income: 1300, net_income: 1150 }
const filter: DrillDownFilter = { title: 'Sample income', type: 'credit', from: '2025-01-01', to: '2025-01-31', account_ids: ['sample-account'] }
const render = (f = filter, adjustment?: IncomeAdjustment) => renderToStaticMarkup(<I18nextProvider i18n={i18n}><TransactionDrillDown filter={f} onClose={() => {}} cardIncomeAdjustment={adjustment} /></I18nextProvider>)

beforeEach(() => {
  harness.queries = []
  vi.clearAllMocks()
  harness.data = {
    items: Array.from({ length: 200 }, (_, i) => ({ id: `sample-${i}`, description: `Real receipt ${i}`, date: '2025-01-02', type: 'credit', amount: 1, amount_primary: 1, currency: 'USD', category: null }) as Transaction),
    total: 250, page: 1, limit: 200,
    summary: { income: 850, expense: 0, net: 850, excluded: 0, currency: 'USD', income_adjustment: booked },
  }
})
describe('authoritative income drill-down rendering and request', () => {
  it('requests the same scoped credit P/L query and uses the unpaginated total', async () => {
    const html = render()
    await harness.queries.find(q => q.queryKey[0] === 'drill-down')!.queryFn()
    expect(transactions.list).toHaveBeenCalledWith(expect.objectContaining({ user_pnl_only: true, type: 'credit', account_ids: ['sample-account'], from: '2025-01-01', to: '2025-01-31', limit: 200 }))
    expect(html).toContain('Showing 200 of 250 real transactions')
    expect(html).toContain('$1,000.00')
    expect(html).toContain('$850.00')
    expect(html).not.toContain('$200.00')
    expect(html).toContain('not just the displayed page')
  })
  it('reconciles card forecast separately without fabricating rows or counts', () => {
    const html = render(filter, card)
    expect(html).toContain('$1,150.00')
    expect(html).toContain('$300.00')
    expect(html).toContain('$850.00')
    expect(html).toContain('Showing 200 of 250 real transactions')
    expect(html.match(/Real receipt /g)).toHaveLength(200)
    expect(harness.queries.find(q => q.queryKey[0] === 'dashboard')?.enabled).toBe(false)
  })
  it('does not replace the authoritative booked query with stale card booked amounts', () => {
    const html = render(filter, { ...card, booked_income: 9999, net_income: 10149, gross_income: 10299 })
    expect(html).toContain('$1,150.00')
    expect(html).not.toContain('$9,999.00')
  })
  it('preserves account and category filters instead of applying a global adjustment', async () => {
    render({ ...filter, account_ids: undefined, account_id: 'sample-account', category_id: 'sample-category' })
    await harness.queries.find(q => q.queryKey[0] === 'drill-down')!.queryFn()
    expect(transactions.list).toHaveBeenCalledWith(expect.objectContaining({ account_id: 'sample-account', category_id: 'sample-category', user_pnl_only: true }))
  })
  it('displays negative net and missing FX even with zero actual credits', () => {
    harness.data = { ...harness.data!, items: [], total: 0, summary: { ...harness.data!.summary!, income: -200, net: -200, income_adjustment: { ...booked, gross_income: 0, booked_income: 0, business_deduction: 200, net_income: -200, missing_fx_count: 1 } } }
    const html = render()
    expect(html).toContain('-$200.00')
    expect(html).toContain('Showing 0 of 0 real transactions')
    expect(html).toContain('Partial Business conversion')
  })
  it('supports servers without bridge metadata without fabricating a split or page total', () => {
    harness.data!.summary!.income_adjustment = undefined
    expect(render()).not.toContain('Personal income reconciliation')
    expect(render()).toContain('$850.00')
    harness.data!.summary = undefined
    expect(render()).not.toContain('$200.00')
    expect(render()).toContain('—')
  })
  it.each([42, null])('does not present an unverified foreign receipt as 1:1 primary currency (%s)', amountPrimary => {
    harness.data!.items = [{ ...harness.data!.items[0], currency: 'EUR', amount: 42, amount_primary: amountPrimary, fx_fallback: true }]
    const html = render()
    expect(html).toContain('€42.00')
    expect(html).toContain('Verified exchange rate unavailable')
    expect(html).not.toContain('$42.00')
  })
})
