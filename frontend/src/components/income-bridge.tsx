import { useTranslation } from 'react-i18next'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import type { IncomeAdjustment } from '@/types'

/** Explanatory read model only — never a bank transaction or cash/bill total. */
export function IncomeBridge({ adjustment }: { adjustment?: IncomeAdjustment | null }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  if (!adjustment) return null
  const money = (amount: number) => mask(new Intl.NumberFormat(locale, {
    style: 'currency', currency: adjustment.currency,
  }).format(amount))
  const splitKnown = adjustment.booked_income != null && adjustment.projected_income != null
  const rows = [
    ...(splitKnown ? [
      { key: 'booked', amount: adjustment.booked_income! },
      { key: 'projected', amount: adjustment.projected_income! },
    ] : []),
    { key: 'gross', amount: adjustment.gross_income },
    { key: 'business', amount: adjustment.business_deduction },
    { key: 'net', amount: adjustment.net_income },
  ]
  return (
    <section aria-label={t('incomeBridge.title')} className="space-y-2 text-xs">
      <dl className="space-y-1">
        {rows.map(row => (
          <div key={row.key} className={`flex justify-between gap-4 ${row.key === 'business' ? 'text-violet-600 dark:text-violet-400' : row.key === 'net' ? 'font-semibold' : 'text-muted-foreground'}`}>
            <dt>{t(`incomeBridge.${row.key}`)}</dt>
            <dd className={`tabular-nums ${row.key === 'net' && row.amount < 0 ? 'text-rose-500' : ''}`}>
              {row.key === 'business' ? '− ' : row.key === 'projected' ? '+ ' : ''}{money(row.amount)}
            </dd>
          </div>
        ))}
      </dl>
      <p className="text-muted-foreground">{t('incomeBridge.explanation')}</p>
      {splitKnown && adjustment.projected_income !== 0 && <p className="text-violet-600 dark:text-violet-400">{t('incomeBridge.forecast')}</p>}
      {!splitKnown && <p className="text-muted-foreground">{t('incomeBridge.forecastUnknown')}</p>}
      {adjustment.missing_fx_count > 0 && (
        <p role="status" className="text-amber-600 dark:text-amber-400">
          {t('incomeBridge.missingFx', { count: adjustment.missing_fx_count })}
        </p>
      )}
    </section>
  )
}
