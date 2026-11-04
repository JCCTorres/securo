import { isValidElement, type ReactNode } from 'react'
import { QueryClient } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import CategoriesPage from './categories'
import { categories as categoriesApi } from '@/lib/api'
import en from '@/locales/en.json'
import type { Category } from '@/types'

type Mutation = {
  mutationFn: (data: unknown) => Promise<unknown>
  onSuccess: () => void
  mutate: ReturnType<typeof vi.fn>
}

// Exercise the page's real event handlers and mutation callbacks without a DOM.
// The hook harness retains state between explicit renders; it does not emulate
// browser events or assert server/API arithmetic.
const harness = vi.hoisted(() => ({
  states: [] as unknown[],
  cursor: 0,
  mutations: [] as Mutation[],
  categories: [] as Category[],
  client: null as QueryClient | null,
  canWrite: true,
}))

vi.mock('react', async (importOriginal) => ({
  ...await importOriginal<typeof import('react')>(),
  useState: (initial: unknown) => {
    const index = harness.cursor++
    if (!(index in harness.states)) harness.states[index] = initial
    return [harness.states[index], (value: unknown) => {
      harness.states[index] = typeof value === 'function'
        ? value(harness.states[index]) : value
    }]
  },
}))
vi.mock('@tanstack/react-query', async (importOriginal) => ({
  ...await importOriginal<typeof import('@tanstack/react-query')>(),
  useQueryClient: () => harness.client,
  useQuery: ({ queryKey }: { queryKey: string[] }) => ({
    data: queryKey[0] === 'categories' ? harness.categories : [],
  }),
  useMutation: (options: Omit<Mutation, 'mutate'>) => {
    const mutation = { ...options, mutate: vi.fn(), isPending: false }
    harness.mutations.push(mutation)
    return mutation
  },
}))
vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: harness.canWrite }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@/lib/api', () => ({
  categories: { list: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn() },
  categoryGroups: { list: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn() },
}))

type Props = Record<string, unknown> & { children?: ReactNode; action?: ReactNode }
function elements(node: ReactNode): { type: unknown; props: Props }[] {
  if (Array.isArray(node)) return node.flatMap(elements)
  if (!isValidElement<Props>(node)) return []
  return [node, ...elements(node.props.children), ...elements(node.props.action)]
}
function render() {
  harness.cursor = 0
  harness.mutations = []
  return elements(CategoriesPage())
}
function businessCheckbox(tree: ReturnType<typeof render>) {
  const checkbox = tree.find(({ props }) => props['aria-describedby'] === 'category-business-description')
  expect(checkbox).toBeDefined()
  return checkbox!.props
}
function click(props: Props) {
  (props.onClick as () => void)()
}
function submit(tree: ReturnType<typeof render>) {
  const form = tree.find(({ type }) => type === 'form')!
  const values = new FormData()
  values.set('name', 'Sample business costs')
  values.set('icon', 'briefcase')
  values.set('color', '#6366f1')
  values.set('group_id', '')
  const NativeFormData = globalThis.FormData
  vi.stubGlobal('FormData', class { constructor() { return values } })
  try {
    (form.props.onSubmit as (event: unknown) => void)({ preventDefault: vi.fn(), currentTarget: {} })
  } finally {
    vi.stubGlobal('FormData', NativeFormData)
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  harness.client?.clear()
  harness.client = new QueryClient()
  harness.states = []
  harness.canWrite = true
  harness.categories = [{
    id: 'sample-category', user_id: 'sample-user', group_id: null,
    name: 'Sample category', icon: 'briefcase', color: '#6366f1',
    is_system: false, treat_as_transfer: true, is_ignored: true, is_business: true,
  }]
})

describe('Business category setting', () => {
  it('defaults off, creates with an explicit boolean, and explains both personal effects', async () => {
    const tree = render()
    const control = businessCheckbox(tree)
    expect(control.checked).toBe(false)
    expect(control.disabled).not.toBe(true)
    expect(tree.some(({ props }) => props.children === 'categories.business')).toBe(true)
    expect(tree.some(({ props }) => props.id === 'category-business-description' && props.children === 'categories.businessDesc')).toBe(true)
    expect(en.categories.businessDesc).toContain('personal expenses')
    expect(en.categories.businessDesc).toContain('reduce personal income')
    expect(en.categories.businessDesc).toContain('same reporting month')
    expect(en.categories.businessDesc).toContain('offset the cost only once')
    expect(en.categories.businessDesc).toContain('Uncheck to reverse')

    submit(tree)
    const mutation = harness.mutations[0]
    const [payload] = mutation.mutate.mock.calls[0]
    expect(payload.is_business).toBe(false)
    await mutation.mutationFn(payload)
    expect(categoriesApi.create).toHaveBeenCalledWith(expect.objectContaining({ is_business: false }))
  })

  it.each([true, false])('saves is_business=%s without erasing legacy flags and invalidates all money views', async (business) => {
    let tree = render()
    click(tree.find(({ props }) => props.title === 'common.edit')!.props)
    tree = render()
    const control = businessCheckbox(tree)
    expect(control.checked).toBe(true)
    expect(control.disabled).not.toBe(true)
    ;(control.onChange as (event: unknown) => void)({ target: { checked: business } })
    tree = render()
    submit(tree)

    const mutation = harness.mutations[1]
    const [payload] = mutation.mutate.mock.calls[0]
    expect(payload).toMatchObject({ is_business: business, is_ignored: true, treat_as_transfer: true })
    await mutation.mutationFn(payload)
    expect(categoriesApi.update).toHaveBeenCalledWith('sample-category', expect.objectContaining({
      is_business: business, is_ignored: true, treat_as_transfer: true,
    }))
    for (const key of ['categories', 'category-groups', 'categoryGroups', 'dashboard', 'reports', 'budgets', 'transactions', 'accounts', 'drill-down']) {
      harness.client!.setQueryData([key, 'sample-scope'], {})
    }
    mutation.onSuccess()
    expect(harness.client!.getQueryCache().getAll().every((query) => query.state.isInvalidated)).toBe(true)
  })

  it('resets Business and ignore state when opening a new category after editing', () => {
    let tree = render()
    click(tree.find(({ props }) => props.title === 'common.edit')!.props)
    tree = render()
    expect(businessCheckbox(tree).checked).toBe(true)
    const addButton = tree.find(({ props }) => props.onClick && elements(props.children).some(({ props: child }) => child.children === 'categories.addCategory'))!
    click(addButton.props)
    tree = render()
    expect(businessCheckbox(tree).checked).toBe(false)
    submit(tree)
    expect(harness.mutations[0].mutate).toHaveBeenCalledWith(expect.objectContaining({
      is_business: false, is_ignored: false, treat_as_transfer: false,
    }))
  })

  it('does not infer Business from a category name or ignored flag', () => {
    harness.categories[0] = { ...harness.categories[0], name: 'Business', is_business: false }
    let tree = render()
    click(tree.find(({ props }) => props.title === 'common.edit')!.props)
    tree = render()
    expect(businessCheckbox(tree).checked).toBe(false)
  })

  it('does not offer edit or create actions to viewers', () => {
    harness.canWrite = false
    const tree = render()
    expect(tree.some(({ props }) => props.title === 'common.edit')).toBe(false)
    expect(tree.some(({ props }) => props.children === 'categories.addCategory')).toBe(false)
  })
})
