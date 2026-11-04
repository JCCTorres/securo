# Business expense income adjustment implementation plan

> **For Hermes:** Use subagent-driven-development to implement this plan, with independent spec and quality reviews before release.

**Goal:** Categorizing a posted transaction as Business excludes its business cost from personal expenses AND deducts that same cost from the same reporting month's personal income, automatically and reversibly.

**Architecture:** Add an explicit default-off category business flag, not merchant-name/UUID detection. Compute a read-model adjustment from real existing transactions, never manufacture accounting transactions or alter provider amounts. Share one scoped/month-aware calculation across dashboard, reports, budget/transaction views and explanatory UI; keep actual cash balances, bank ledger, investment totals and sync unchanged.

**Tech Stack:** FastAPI, SQLAlchemy async/PostgreSQL, Alembic, pytest/SQLite; React/TypeScript/Vite/Vitest.

## Scope and privacy
- Branch from deployed baseline `10a3aca8ce00f21f141d25348c73c9dfdeb6aaee` in an isolated feature worktree.
- GitHub fork `JCCTorres/securo`; deployment baseline branch `household`. Do not merge unrelated upstream updates. Do not push to upstream `securo-finance/securo`.
- No production changes during implementation. Parent owns commit/push/PR, CI, deployment, scoped activation and rollback.
- This repository is PUBLIC. No real household amounts, bank identifiers, personal names, tokens, snapshots or private absolute integration paths in tracked files/tests/PRs. Use clearly synthetic examples.
- Do not modify original checkout, private integration scripts, existing failed-maintenance scripts, credentials or global configuration.

## Accepted semantics
1. `Category.is_business` (default false) is the explicit opt-in. Existing categorization UI is the marking workflow; add a clearly labelled category checkbox. User can mark/unmark or change the transaction category and immediately see reversal without resync.
2. Ordinary ignored categories are NOT Business. Do not infer from category names. An explicitly flagged Business category may retain legacy `is_ignored`/`treat_as_transfer` values; they do not suppress the business computation. Explicit ignored transactions, opening balances, settlement sources and actual paired transfers remain ineligible for adjustment.
3. Business flagged posted debits are not personal expenses. Business flagged credits are not personal income and offset the business cost (vendor refunds or company reimbursements ALREADY excluded from gross personal income). Compute per reporting month `deduction = max(0, eligible business debits - eligible business credits)`. Do not deduct an already-excluded reimbursement twice. Do not carry deductions across months. This is a managerial income metric, not a claim that reimbursement occurred.
4. `net personal income = gross eligible personal income - deduction`. Do not cap deduction to gross income: a month may be negative; show it truthfully and handle savings-rate denominators safely.
5. Use identical reporting-date rules (cash/accrual and explicit effective bill date) and scope (workspace/user/account/collection) as the consuming view. For multi-month totals sum per-month deductions, not `max(0, aggregate debits-credits)` across months. Foreign amounts must use stored transaction-date primary values or a real exact-date cached rate, per-transaction cent rounding. Never treat missing foreign FX as 1:1; expose missing-conversion counts/warnings. Parent can perform scoped historical FX backfill before activation.
6. Pending entries do NOT reduce realized income. Marked pending business entries stay outside personal expense/budget actuals consistently; projections, if shown, must remain explicitly projected and not silently counted as realized.
7. Default-off behavior remains identical. Actual current/historical bank balances, opening-balance reconciliation, asset values, goals, transfer links, raw bank descriptions/amounts/dates/external IDs and provider sync must not change. No auxiliary accounts/transactions, invented transfer pairs or source relabeling.
8. Reports must remain honest beyond headline totals: summary, time series, category composition, Sankey and category trends must disclose/reconcile the business deduction. Never classify a business correction as an investment contribution. Preserve true investment contributions. Use explicit gross income / business deduction / net personal income metadata and labelled nonbank explanatory presentation. If a chart needs a business-cost node, label it Business, not investments or personal expense, and ensure the flow sums.
9. Dashboard Income drill-down must reconcile to the card: keep bank credits untouched and show gross receipts, deduction, net income explicitly. Do not fabricate a transaction item, alter real transaction count, or sum only the first 200 rows as authoritative total. Account/category-filtered views must not apply an unrelated global deduction. Reclassification invalidates relevant React Query keys.
10. Do not silently change shared-expense/group semantics; use owner shares where existing personal views do, or fail/report a demonstrated ambiguity to parent. No new debt/settlement records.

## Task 1: explicit category contract and migration (RED/GREEN)
Files: `backend/app/models/category.py`, `backend/app/schemas/category.py`, category API/service serializers, next Alembic migration, `backend/tests/test_business_income_adjustment.py`.
- Write failing create/read/update category flag tests, default-off parity and workspace isolation.
- Add default-false boolean with migration upgrade/downgrade. No automatic name-based data backfill.
- Verify flag round-trip and migration ordering; unrelated category fields retain behavior.

## Task 2: one calculation and backend consumers (RED/GREEN)
Files: new `backend/app/services/business_income_service.py` (or equivalent focused helper), `_query_filters.py`, `dashboard_service.py`, `report_service.py`, `budget_service.py` and transaction/account summary APIs where needed; relevant response schemas.
- Write tests against native services/APIs, not arithmetic-only helpers.
- Synthetic control: gross income 1000, business debit 200 -> personal income 800; no personal expense for 200; balances identical. Add excluded business reimbursement 50 -> deduction 150 / income 850, not 750. Unmark reverses exactly.
- Distinguishing tests: unrelated ignored expense; transaction-ignored business duplicate; pending; business refund; debit/reimbursement in different months; expense exceeds income; same nominal foreign amount with different historic FX; missing FX warning; workspace/account scoping; transfer/opening/settlement exclusions; cash/accrual period; legacy ignored-category + explicit business flag; shared expense ownership.
- Compare full report composition and trends. Include a regression that would fail if business amount appears in investment contributions.
- Verify no inserted bank rows or account/asset/goal changes through each transition.

## Task 3: UI and explanation (RED/GREEN)
Files: `frontend/src/pages/categories.tsx`, `frontend/src/types/index.ts`, API hooks, `pages/dashboard.tsx`, `components/transaction-drill-down.tsx`, relevant report/Sankey components and locale files.
- Expose the business category setting and describe both effects.
- Show gross income, Business deduction, net personal income with same-month scope and missing-FX warning. Use backend authoritative totals for drill-down, not truncated client sums.
- Make mark/unmark/update invalidate income, reports and expense views.
- Add focused Vitest tests for transform/render semantics and chart grouping where existing infrastructure permits; run TS build and lint.

## Task 4: independent verification and GitHub delivery (parent)
- Run focused backend tests first, then full affected/backend suite, ruff and ty, frontend tests/lint/build. Use Python 3.13 + uv locked dependencies and Node package-lock. Do not weaken CI or rewrite unrelated lockfiles.
- Independent spec review and quality review on exact candidate. Fix demonstrated contract violations; no optional-framework expansion.
- Commit only intended source/test/docs files, scan for private data, push feature branch to the user's fork and open PR against `household`.
- Require exact-head CI and read back PR URL/head/status. No upstream PR or automatic production deploy from the writer.

## Task 5: controlled activation (parent, after verification)
- Establish current deployed image/source, build primitive, online backup, reversible migration and rollback image before deploy. Exercise actual PostgreSQL migration/read behavior before activation.
- Deploy source corresponding to verified GitHub SHA; verify backend/worker/beat/frontend health and migration version.
- Explicitly enable the existing user-selected Business and business-reimbursement categories only in the intended workspace; do not hardcode them into code/migrations. Verify/backfill exact-date FX where missing without replacing bank amounts. Keep private evidence outside git.
- Verify real October card, drill-down gross/deduction/net, full reporting (including investment composition), current/daily historical bank balances, investments, budget limits, goals, categorization reversal and preserved provider identities. Retain rollback plan.
- Update local reimbursement ledger so it reconciles the new native deduction without subtracting it twice; archive superseded control policy and preserve original evidence. Do not claim deployment success from tests alone.

## Execution commands
From worktree backend: `uv sync --frozen --extra dev --python 3.13`; `uv run --frozen pytest tests/test_business_income_adjustment.py -q`; `uv run --frozen ruff check .`; `uv run --frozen ty check .`; `uv run --frozen pytest -n auto --dist loadfile` (bounded workers if resources require).
From worktree frontend: `npm ci`; `npm test`; `npm run lint`; `npm run build`.
Use native `C:/...` paths with Windows tools. Never load production environment into tests. Track blockers honestly; no fabricated API results.
