// Backend error / warning {code, params} → text in the interface language.
// Texts live in i18n/locales/<lang>/errors.json under errors.<code>; `make i18n-check`
// fails when a code the backend can send has no text. Unknown code → the code itself.
import { toApiError } from '~/plugins/api'

type Coded = { code: string; params?: Record<string, unknown> }
type FormatError = { loc: string; msg: string; type?: string }

// what is wrong with a place of a scenario file → the text that says it (editor.file.err.*)
const FORMAT_KINDS: Record<string, string> = {
  missing: 'missing',
  extra_forbidden: 'extra',
  string_pattern_mismatch: 'pattern',
  enum: 'choice',
  literal_error: 'choice',
  string_type: 'text',
  dict_type: 'nested',
  model_type: 'not_scenario',
  list_type: 'list',
  too_short: 'empty_list',
  int_parsing: 'number',
  int_type: 'number',
  int_from_float: 'number',
  float_parsing: 'number',
  float_type: 'number',
  decimal_parsing: 'number',
  decimal_type: 'number',
  bool_parsing: 'bool',
  bool_type: 'bool',
}
// a text is «a string or {language: text}»: the checker names both readings of a place
const isReading = (part: string) => part === 'str' || part.startsWith('dict[')

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
      params.errors = problemLines(err).join('; ')
    }
    const key = `errors.${err.code}`
    return te(key) ? t(key, params) : err.code
  }

  // The problems of a scenario, one per line: where («fields → №3 → question», counted
  // from one, as a person counts) and what, in words when the kind of the problem is known.
  function problemLines(err: Coded): string[] {
    if (err.code !== 'scenario.invalid' || !Array.isArray(err.params?.errors)) return [codeText(err)]
    const lines: string[] = []
    const told = new Set<string>()
    for (const e of err.params!.errors as FormatError[]) {
      const parts = e.loc ? e.loc.split('.') : []
      const where = parts
        .filter((part) => !isReading(part))
        .map((part) => (/^\d+$/.test(part) ? `№${Number(part) + 1}` : part))
        .join(' → ')
      // one place read two ways is one problem: the first reading says it
      if (parts.some(isReading)) {
        if (told.has(where)) continue
        told.add(where)
      }
      const kind = FORMAT_KINDS[e.type || '']
      const what = kind
        ? t(`editor.file.err.${kind}`, { values: (e.msg.match(/'[^']+'/g) || []).map((v) => v.slice(1, -1)).join(', ') })
        : e.msg.replace(/^Value error, /, '')
      lines.push(where ? `${where}: ${what}` : what)
    }
    return lines
  }

  function errorText(e: unknown): string {
    if (e && typeof e === 'object' && 'code' in e && !(e instanceof Error)) {
      return codeText(e as Coded)
    }
    return codeText(toApiError(e))
  }

  return { errorText, codeText, problemLines }
}
