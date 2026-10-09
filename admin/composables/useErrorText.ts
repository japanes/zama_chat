// Backend error / warning {code, params} → text in the interface language.
// Texts live in i18n/locales/<lang>/errors.json under errors.<code>; `make i18n-check`
// fails when a code the backend can send has no text. Unknown code → the code itself.
import { toApiError } from '~/plugins/api'

type Coded = { code: string; params?: Record<string, unknown> }

export function useErrorText() {
  const { t, te } = useI18n()

  function value(key: string, v: unknown): string {
    if (Array.isArray(v)) {
      return v
        .map((x) => (x && typeof x === 'object' ? JSON.stringify(x) : value(key, x)))
        .join(', ')
    }
    if (v && typeof v === 'object') return JSON.stringify(v)
    // role names are translated; everything else (keys, models, languages) is shown as is
    if ((key === 'need' || key === 'role') && typeof v === 'string' && te(`roles.${v}`)) {
      return t(`roles.${v}`)
    }
    // the role of a model goes by what it does: «Answer to the client», not `reply`
    if (key === 'role' && typeof v === 'string' && te(`models.role.${v}`)) {
      return `«${t(`models.role.${v}`)}»`
    }
    return v === null || v === undefined ? '' : String(v)
  }

  function codeText(err: Coded): string {
    const params: Record<string, string> = {}
    for (const [k, v] of Object.entries(err.params || {})) params[k] = value(k, v)
    if (err.code === 'request.invalid' && Array.isArray(err.params?.fields)) {
      params.fields = (err.params!.fields as { loc: string[] }[])
        .map((f) => f.loc.filter((p) => p !== 'body').join('.'))
        .join(', ')
    }
    if (err.code === 'scenario.invalid' && Array.isArray(err.params?.errors)) {
      params.errors = (err.params!.errors as { loc: string; msg: string }[])
        .map((e) => `${e.loc}: ${e.msg}`)
        .join('; ')
    }
    const key = `errors.${err.code}`
    return te(key) ? t(key, params) : err.code
  }

  function errorText(e: unknown): string {
    if (e && typeof e === 'object' && 'code' in e && !(e instanceof Error)) {
      return codeText(e as Coded)
    }
    return codeText(toApiError(e))
  }

  return { errorText, codeText }
}
