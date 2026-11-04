import { renderToStaticMarkup } from 'react-dom/server'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { describe, expect, it, vi } from 'vitest'
import { IncomeBridge } from './income-bridge'
import { CashflowSankey } from './reports/CashflowSankey'
import en from '@/locales/en.json'
import type { IncomeAdjustment, ReportCompositionItem } from '@/types'

vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US' }))
vi.mock('@/hooks/use-privacy-mode', () => ({ usePrivacyMode: () => ({ mask: (v: string) => v, privacyMode: false, MASK: '***' }) }))
const i18n = createInstance()
void i18n.init({ lng: 'en', resources: { en: { translation: en } }, initImmediate: false })
const bridge: IncomeAdjustment = {
  booked_income: 1000, projected_income: 300, gross_income: 1300,
  business_deduction: 150, net_income: 1150, currency: 'USD', missing_fx_count: 0, nonbank: true,
  months: [{ month: '2025-01', debits: 200, credits: 50, deduction: 150, missing_fx_count: 0 }],
}
const render = (adjustment?: IncomeAdjustment | null) => renderToStaticMarkup(<I18nextProvider i18n={i18n}><IncomeBridge adjustment={adjustment} /></I18nextProvider>)

describe('IncomeBridge actual rendering', () => {
  it('renders booked + explicit forecast - net monthly costs, without double subtracting refunds', () => {
    const html = render(bridge)
    for (const text of ['Booked receipts', '$1,000.00', 'Projected income (not received)', '$300.00', '$1,300.00', '− $150.00', '$1,150.00', 'not received money']) expect(html).toContain(text)
    expect(html).not.toContain('− $200.00')
    expect(html).not.toContain('role="status"')
  })
  it('shows signed negative net and a partial-conversion warning, not a 1:1 total', () => {
    const html = render({ ...bridge, projected_income: 0, gross_income: 1000, business_deduction: 1200, net_income: -200, missing_fx_count: 2 })
    expect(html).toContain('-$200.00')
    expect(html).toContain('text-rose-500')
    expect(html).toContain('2 entries lack verified exchange rates')
    expect(html).toContain('No 1:1 conversion is assumed')
    expect(html).not.toContain('+-')
  })
  it('does not invent a split for older servers', () => {
    const html = render({ ...bridge, booked_income: undefined, projected_income: undefined })
    expect(html).toContain('does not disclose the booked/forecast split')
    expect(html).not.toContain('<dt>Booked receipts</dt>')
    expect(html).not.toContain('<dt>Projected income (not received)</dt>')
    expect(html).toContain('$1,150.00')
  })
  it('is additive and optional for old servers', () => {
    expect(render()).toBe('')
    expect(render(null)).toBe('')
  })
})

describe('CashflowSankey actual accessible rendering', () => {
  // ICU versions differ on compact USD's trailing .0; keep exact numeric values.
  const items: ReportCompositionItem[] = [
    { key: 'salary', label: 'Sample salary', group: 'income', value: 1000, color: '#000' },
    { key: 'business-deduction', label: 'Business', group: 'business', value: 800, color: '#000' },
    { key: 'food', label: 'Sample food', group: 'expenses', value: 300, color: '#000' },
    { key: 'invest', label: 'True investment', group: 'investments', value: 200, color: '#000' },
  ]
  const chart = (composition: ReportCompositionItem[]) => renderToStaticMarkup(<I18nextProvider i18n={i18n}><CashflowSankey composition={composition} currency="USD" locale="en-US" /></I18nextProvider>)
  it('renders independent business, true investments and explicit funding gap even before layout', () => {
    const html = chart(items)
    expect(html).toMatch(/<td>Business<\/td><td>\$800(?:\.0)?<\/td>/)
    expect(html).toMatch(/<td>True investment<\/td><td>\$200(?:\.0)?<\/td>/)
    expect(html).toMatch(/<td>Funding deficit<\/td><td>\$300(?:\.0)?<\/td>/)
    expect(html).toContain('its funding source is not inferred')
    expect(html).not.toContain('<td>Business</td><td>$0</td>')
  })
  it('retains a business-only negative-income case', () => {
    const html = chart([items[1]])
    expect(html).toMatch(/<td>Business<\/td><td>\$800(?:\.0)?<\/td>/)
    expect(html).toMatch(/<td>Funding deficit<\/td><td>\$800(?:\.0)?<\/td>/)
  })
  it('shows only the true residual after all outflows, not net income as gross', () => {
    const html = chart(items.map(c => c.group === 'business' ? { ...c, value: 150 } : c))
    expect(html).toMatch(/<td>Surplus<\/td><td>\$350(?:\.0)?<\/td>/)
    expect(html).not.toContain('<td>Funding deficit</td>')
  })
})
