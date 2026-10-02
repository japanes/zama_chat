// Dates, numbers and money in the format of the interface language (Intl).
export function useFormat() {
  const { locale } = useI18n()
  const tag = computed(() => ({ en: 'en-GB', uk: 'uk-UA', ru: 'ru-RU' })[locale.value] || locale.value)

  function dateTime(iso?: string | null) {
    if (!iso) return '—'
    return new Intl.DateTimeFormat(tag.value, { dateStyle: 'medium', timeStyle: 'short' }).format(
      new Date(iso),
    )
  }
  function date(iso?: string | null) {
    if (!iso) return '—'
    return new Intl.DateTimeFormat(tag.value, { dateStyle: 'medium' }).format(new Date(iso))
  }
  function time(iso?: string | null) {
    if (!iso) return '—'
    return new Intl.DateTimeFormat(tag.value, { timeStyle: 'short' }).format(new Date(iso))
  }
  function relative(iso?: string | null) {
    if (!iso) return '—'
    const diff = (new Date(iso).getTime() - Date.now()) / 1000
    const rtf = new Intl.RelativeTimeFormat(tag.value, { numeric: 'auto' })
    const abs = Math.abs(diff)
    if (abs < 60) return rtf.format(Math.round(diff), 'second')
    if (abs < 3600) return rtf.format(Math.round(diff / 60), 'minute')
    if (abs < 86400) return rtf.format(Math.round(diff / 3600), 'hour')
    if (abs < 86400 * 30) return rtf.format(Math.round(diff / 86400), 'day')
    return date(iso)
  }
  function num(n?: number | string | null, digits = 0) {
    if (n === null || n === undefined || n === '') return '—'
    return new Intl.NumberFormat(tag.value, { maximumFractionDigits: digits }).format(Number(n))
  }
  // LLM costs are fractions of a cent: up to 6 digits, at least 2
  function money(n?: number | string | null) {
    if (n === null || n === undefined || n === '') return '—'
    const v = Number(n)
    return new Intl.NumberFormat(tag.value, {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 2,
      maximumFractionDigits: v !== 0 && Math.abs(v) < 0.01 ? 6 : 4,
    }).format(v)
  }
  function ms(n?: number | null) {
    if (n === null || n === undefined) return '—'
    return n < 1000 ? `${n} ms` : `${num(n / 1000, 1)} s`
  }
  return { dateTime, date, time, relative, num, money, ms, tag }
}
