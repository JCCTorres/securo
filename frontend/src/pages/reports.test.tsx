import { renderToStaticMarkup } from 'react-dom/server'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ReportsPage from './reports'
import en from '@/locales/en.json'
import type { ReportResponse } from '@/types'

const harness = vi.hoisted(() => ({ tab: 'income_expenses', data: undefined as ReportResponse | undefined }))
// Select the report tab through its initial state; all component rendering and
// report transforms stay real. No DOM/chart-layout claim is made by SSR tests.
vi.mock('react', async importOriginal => {
  const original = await importOriginal<typeof import('react')>()
  return { ...original, useState: (initial: unknown) => original.useState(initial === 'net_worth' ? harness.tab : initial === 'netWorth' ? 'net' : initial === 'byExpenses' ? 'business' : initial) }
})
vi.mock('@tanstack/react-query', () => ({ useQuery: () => ({ data: harness.data, isLoading: false }) }))
vi.mock('@/lib/api', () => ({ reports: {} }))
vi.mock('@/contexts/collection-filter-context', () => ({ useCollectionFilter: () => ({ activeAccountIds: null, activeWalletIds: null }) }))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }) }))
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US' }))
vi.mock('@/hooks/use-privacy-mode', () => ({ usePrivacyMode: () => ({ mask: (v: string) => v, privacyMode: false, MASK: '***' }) }))
const i18n = createInstance()
void i18n.init({ lng: 'en', resources: { en: { translation: en } }, initImmediate: false })
const render = () => renderToStaticMarkup(<I18nextProvider i18n={i18n}><ReportsPage /></I18nextProvider>)
beforeEach(() => {
  harness.tab = 'income_expenses'
  harness.data = {
    meta: { type: 'income_expenses', currency: 'USD', interval: 'monthly', series_keys: ['income', 'expenses'], income_adjustment: { booked_income: 1000, projected_income: 300, gross_income: 1300, business_deduction: 150, net_income: 1150, currency: 'USD', missing_fx_count: 1, months: [], nonbank: true } },
    summary: { primary_value: 850, change_amount: 0, change_percent: null, breakdowns: [
      { key: 'income', label: 'Income', value: 1150, color: '#10B981' },
      { key: 'expenses', label: 'Expenses', value: 300, color: '#F43F5E' },
      { key: 'grossIncome', label: 'Gross income', value: 1300, color: '#10B981' },
      { key: 'businessDeduction', label: 'Business', value: 150, color: '#8B5CF6' },
      { key: 'netPersonalIncome', label: 'Net personal income', value: 1150, color: '#10B981' },
      { key: 'netIncome', label: 'Net income', value: 850, color: '#6366F1' },
    ] },
    composition: [
      { key: 'sample-income', label: 'Sample income', group: 'income', value: 1300, color: '#10B981' },
      { key: 'business-deduction', label: 'Business', group: 'business', value: 150, color: '#8B5CF6' },
      { key: 'sample-expense', label: 'Sample expense', group: 'expenses', value: 300, color: '#F43F5E' },
      { key: 'sample-invest', label: 'True investment', group: 'investments', value: 200, color: '#0EA5E9' },
    ],
    trend: [{ date: '2025-01', value: 850, change: null, breakdowns: { income: 1150, expenses: 300, grossIncome: 1300, businessDeduction: 150, netPersonalIncome: 1150 } }],
    category_trend: [{ key: 'business-deduction', label: 'Business', group: 'business', color: '#8B5CF6', total: 150, series: [{ date: '2025-01', value: -50, breakdowns: {}, change: null }] }],
  }
})
describe('Business report integration rendering', () => {
  it('discloses report forecasts, FX and net-after-personal-expense semantics', () => {
    const html = render()
    expect(html).toContain('Projected income (not received)')
    expect(html).toContain('$1,150.00')
    expect(html).toContain('$850.00')
    expect(html).toContain('additionally subtracts personal expenses')
    expect(html).toContain('Partial Business conversion')
    expect(html).toContain('Business</button>')
    expect(html).toContain('$150')
  })
  it('does not hide negative net income in the summary', () => {
    harness.data!.summary.breakdowns[0].value = -200
    const html = render()
    expect(html).toContain('-$200.00')
  })
  it('renders the Money Map from gross categories, business once, and actual investments', () => {
    harness.tab = 'money_map'
    const html = render()
    // Compact-number ICU formatting may retain .0; amounts must still be exact.
    expect(html).toMatch(/<td>Business<\/td><td>\$150(?:\.0)?<\/td>/)
    expect(html).toMatch(/<td>True investment<\/td><td>\$200(?:\.0)?<\/td>/)
    expect(html).toMatch(/<td>Surplus<\/td><td>\$650(?:\.0)?<\/td>/)
  })
  it('accepts an old-server report without new metadata', () => {
    harness.data!.meta.income_adjustment = undefined
    expect(render()).not.toContain('Personal income reconciliation')
  })
})
