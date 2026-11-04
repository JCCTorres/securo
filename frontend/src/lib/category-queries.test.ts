import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it } from 'vitest'
import { invalidateCategoryQueries } from './invalidate-queries'

const affectedKeys = [
  ['categories'],
  ['category-groups'],
  ['categoryGroups'],
  ['dashboard', 'summary', '2025-04', ['sample-account']],
  ['dashboard', 'spending', '2025-04'],
  ['reports', 'cash-flow', '2025-04'],
  ['budgets', 'comparison', '2025-04'],
  ['transactions', { category_id: 'sample-category' }],
  ['transactions', 'cumulative', '2025-04'],
  ['drill-down', { type: 'credit' }],
  ['accounts', 'sample-account', 'summary'],
]

describe('category classification invalidation', () => {
  it('marks every category and financial view stale, including inactive scoped variants', () => {
    const client = new QueryClient()
    for (const key of affectedKeys) client.setQueryData(key, { synthetic: true })
    client.setQueryData(['currencies'], ['EUR'])

    invalidateCategoryQueries(client)

    for (const key of affectedKeys) {
      expect(client.getQueryState(key)?.isInvalidated, JSON.stringify(key)).toBe(true)
    }
    expect(client.getQueryState(['currencies'])?.isInvalidated).toBe(false)
    client.clear()
  })
})
