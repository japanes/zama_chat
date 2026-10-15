// Scenario definition helpers for the editor. The server is the judge (POST …/check);
// here only what the form needs: a blank scenario, cleaning before sending, hints.

export type LText = string | Record<string, string>

export const FIELD_TYPES = [
  'text',
  'choice',
  'yes_no',
  'catalog',
  'date',
  'time',
  'phone',
  'email',
  'number',
  'list',
  'attachment',
  'receipt',
] as const
// filled from the client's files, not from words
export const ATTACHMENT_TYPES = ['attachment', 'receipt']
export const ATTACHMENT_KINDS = ['image', 'video', 'audio', 'file'] as const
export const EVENT_RESULTS = ['completed', 'cancelled', 'reopen'] as const
export const COMPLETIONS = ['all_fields', 'explicit_yes', 'external_event', 'receipt', 'by_caller'] as const
// the defaults of the format (backend scenarios/schema.py): left out when sending
export const SHOW_IF_DEFAULTS = { on_unknown: 'ask', recheck: 'every_turn' }
export const ADDED_DEFAULTS = { wording: 'own_words', close_after: 3 }
export const ACTION_EVENTS = [
  'started',
  'field_filled',
  'field_stuck',
  'all_fields',
  'confirmed',
  'waiting',
  'completed',
  'cancelled',
  'abandoned',
  'silence',
] as const
export const ACTIONS = ['http_post', 'notify_telegram', 'notify_email', 'message_client', 'handover'] as const
export const NOTIFY_ACTIONS = ['notify_telegram', 'notify_email']
export const KEY_RE = /^[a-z][a-z0-9]*(_[a-z0-9]+)*$/

export function newField(n: number): any {
  return {
    key: `field_${n}`,
    // editor only: the key follows the name until somebody types it by hand
    _auto: true,
    type: 'text',
    title: null,
    question: {},
    options: [],
    filters: [],
    ask_after: [],
    // asked only when the answers say it applies (null — always asked)
    show_if: null,
    // choice only: 'caller' — the options come with the start of a dialogue
    options_from: null,
    ...typedDefaults(),
  }
}

// «when to ask this question»: one yes/no question, enough by itself
export function newShowIf(): any {
  return { questions: [{ key: 'applies', ask: {}, weight: 1 }], threshold: 1, ...SHOW_IF_DEFAULTS }
}

// settings of every typed field, so the form can bind to them whatever the type is;
// cleanDefinition sends only the one of the field's type
function typedDefaults(): any {
  return {
    catalog: { categories: [], max_options: 5 },
    date: { allow_past: false, max_days_ahead: '' },
    number: { min: '', max: '', integer: false },
    items: { min_items: 1, max_items: 10, item_fields: [] },
    attachment: { kinds: ['image', 'file'], max_items: 1 },
    receipt: { amount: '', amount_field: '', quantity_field: '', currency: '', recipients: [], max_age_days: 3, tolerance: 0 },
  }
}

function num(value: unknown): number | undefined {
  return value === '' || value == null || Number.isNaN(Number(value)) ? undefined : Number(value)
}

export function newDefinition(language: string): any {
  return {
    key: '',
    title: {},
    language,
    start: { questions: [{ key: 'wants_it', ask: {}, weight: 1 }], threshold: 1 },
    fields: [newField(1)],
    completion: 'all_fields',
    confirm: null,
    events_list: [],
    waiting: { hours: '', say: {} },
    incompatible_with: [],
    resume: { mode: 'always' },
    cancel: {},
    actions: [],
    ask_rules: [],
    history_messages: '',
    added: { ...ADDED_DEFAULTS, max_fields: '' },
  }
}

// The form keeps every list and object present; the server wants no empty texts, no
// unused keys (its models forbid extra keys).
function text(value: unknown): LText | undefined {
  if (typeof value === 'string') return value.trim() ? value.trim() : undefined
  if (value && typeof value === 'object') {
    const out: Record<string, string> = {}
    for (const [lang, v] of Object.entries(value as Record<string, unknown>)) {
      if (typeof v === 'string' && v.trim()) out[lang] = v.trim()
    }
    return Object.keys(out).length ? out : undefined
  }
  return undefined
}

function compact<T extends Record<string, any>>(obj: T): T {
  for (const k of Object.keys(obj)) if (obj[k] === undefined || obj[k] === null || obj[k] === '') delete obj[k]
  return obj
}

// texts left after dropping the empty ones; none → left out
function listOf(values: (LText | undefined)[]): LText[] | undefined {
  const out = values.filter((v): v is LText => v !== undefined)
  return out.length ? out : undefined
}

// a value equal to the format's default is left out: the server fills it in the same way
function unlessDefault(value: unknown, fallback: unknown): any {
  return value === undefined || value === null || value === '' || value === fallback ? undefined : value
}

function cleanShowIf(show: any): any {
  if (!show || !(show.questions || []).length) return undefined
  return compact({
    questions: show.questions.map((q: any) => ({ key: q.key, ask: text(q.ask) ?? {}, weight: Number(q.weight) || 1 })),
    threshold: num(show.threshold),
    on_unknown: unlessDefault(show.on_unknown, SHOW_IF_DEFAULTS.on_unknown),
    recheck: unlessDefault(show.recheck, SHOW_IF_DEFAULTS.recheck),
  })
}

// completion by_caller only: what the bot does with the questions the caller adds
function cleanAdded(d: any): any {
  if (d.completion !== 'by_caller' || !d.added) return undefined
  const out = compact({
    wording: unlessDefault(d.added.wording, ADDED_DEFAULTS.wording),
    close_after: unlessDefault(num(d.added.close_after), ADDED_DEFAULTS.close_after),
    max_fields: num(d.added.max_fields),
  })
  return Object.keys(out).length ? out : undefined
}

function cleanWaiting(d: any): any {
  if (d.completion === 'external_event') return num(d.waiting?.hours) ? { hours: num(d.waiting.hours) } : undefined
  if (d.completion === 'by_caller') {
    const say = text(d.waiting?.say)
    return say ? { say } : undefined
  }
  return undefined
}

// params of an action: only the ones its handler reads (others are kept for the JSON mode)
function actionParams(a: any): any {
  const p: Record<string, any> = { ...(a.params || {}) }
  if (NOTIFY_ACTIONS.includes(a.do)) p.notifier = (p.notifier || '').trim() || undefined
  else delete p.notifier
  if (a.do === 'message_client') p.text = text(p.text)
  else delete p.text
  const out = compact(p)
  return Object.keys(out).length ? out : undefined
}

export function cleanDefinition(d: any): any {
  const start = d.start || {}
  return compact({
    key: d.key,
    title: text(d.title) ?? {},
    language: d.language,
    description: text(d.description),
    start: compact({
      questions: (start.questions || []).map((q: any) =>
        q.field
          ? { field: q.field, weight: Number(q.weight) || 1 }
          : { key: q.key, ask: text(q.ask) ?? {}, weight: Number(q.weight) || 1 },
      ),
      threshold: start.threshold === '' || start.threshold == null ? undefined : Number(start.threshold),
    }),
    fields: (d.fields || []).map((f: any) =>
      compact({
        key: f.key,
        type: f.type || 'text',
        title: text(f.title),
        question: text(f.question) ?? {},
        description: text(f.description),
        options:
          f.type === 'choice' && f.options_from !== 'caller'
            ? (f.options || []).map((o: any) => ({ key: o.key, label: text(o.label) ?? {} }))
            : undefined,
        options_from: f.type === 'choice' && f.options_from === 'caller' ? 'caller' : undefined,
        catalog:
          f.type === 'catalog'
            ? compact({
                categories: (f.catalog?.categories || []).map((c: string) => c.trim()).filter(Boolean),
                max_options: f.catalog?.max_options === '' || f.catalog?.max_options == null ? undefined : Number(f.catalog.max_options),
              })
            : undefined,
        ...typedSettings(f),
        filters: (f.filters || []).length
          ? f.filters.map((x: any) => compact({ key: x.key, ask: text(x.ask) ?? {}, yes: text(x.yes), no: text(x.no) }))
          : undefined,
        ask_after: (f.ask_after || []).length ? [...f.ask_after] : undefined,
        show_if: cleanShowIf(f.show_if),
      }),
    ),
    completion: d.completion || 'all_fields',
    confirm: d.confirm ? compact({ question: text(d.confirm.question) }) : undefined,
    events:
      d.completion === 'external_event' && (d.events_list || []).length
        ? Object.fromEntries(
            d.events_list.map((e: any) => [
              (e.name || '').trim(),
              compact({
                result: e.result || 'completed',
                reopen_fields: e.result === 'reopen' ? [...(e.reopen_fields || [])] : undefined,
                say: text(e.say),
              }),
            ]),
          )
        : undefined,
    waiting: cleanWaiting(d),
    incompatible_with: (d.incompatible_with || []).length ? [...d.incompatible_with] : undefined,
    resume:
      d.resume?.mode === 'within_hours'
        ? compact({ mode: 'within_hours', hours: d.resume.hours === '' || d.resume.hours == null ? undefined : Number(d.resume.hours) })
        : { mode: 'always' },
    cancel: compact({ description: text(d.cancel?.description) }),
    actions: (d.actions || []).map((a: any) =>
      compact({
        on: a.on,
        do: a.do || 'http_post',
        endpoint: a.do === 'http_post' || !a.do ? a.endpoint || undefined : undefined,
        after_minutes: a.on === 'silence' && a.after_minutes !== '' && a.after_minutes != null ? Number(a.after_minutes) : undefined,
        status: a.status || undefined,
        field: ['field_filled', 'field_stuck'].includes(a.on) && a.field ? a.field : undefined,
        params: actionParams(a),
        requires_approval: a.requires_approval ? true : undefined,
      }),
    ),
    ask_rules: listOf((d.ask_rules || []).map(text)),
    history_messages: num(d.history_messages),
    added: cleanAdded(d),
  })
}

// A definition from the server (or YAML) → the shape the form edits: every list present.
export function toForm(d: any): any {
  const out = JSON.parse(JSON.stringify(d || {}))
  out.start ||= { questions: [] }
  out.start.questions ||= []
  for (const q of out.start.questions) q.weight ??= 1
  out.fields ||= []
  for (const f of out.fields) {
    f.type ||= 'text'
    f.options ||= []
    f.filters ||= []
    f.ask_after ||= []
    const defaults = typedDefaults()
    for (const k of Object.keys(defaults)) f[k] = { ...defaults[k], ...(f[k] || {}) }
    for (const k of ['min', 'max']) f.number[k] ??= ''
    f.date.max_days_ahead ??= ''
    for (const k of ['amount', 'amount_field', 'quantity_field', 'currency']) f.receipt[k] ??= ''
    f.receipt.recipients ||= []
    f.items.item_fields ||= []
    f.options_from ??= null
    if (f.show_if) {
      f.show_if.questions ||= []
      for (const q of f.show_if.questions) q.weight ??= 1
      f.show_if.threshold ??= ''
      f.show_if.on_unknown ||= SHOW_IF_DEFAULTS.on_unknown
      f.show_if.recheck ||= SHOW_IF_DEFAULTS.recheck
    } else {
      f.show_if = null
    }
  }
  out.completion ||= 'all_fields'
  out.confirm ??= null
  out.events_list = Object.entries(out.events || {}).map(([name, e]: [string, any]) => ({
    name,
    result: e.result || 'completed',
    reopen_fields: e.reopen_fields || [],
    say: e.say ?? {},
  }))
  delete out.events
  out.waiting = { hours: out.waiting?.hours ?? '', say: out.waiting?.say ?? {} }
  out.incompatible_with ||= []
  out.resume ||= { mode: 'always' }
  out.cancel ||= {}
  out.actions ||= []
  for (const a of out.actions) {
    a.do ||= 'http_post'
    a.params ||= {}
  }
  out.ask_rules ||= []
  out.history_messages ??= ''
  out.added = { ...ADDED_DEFAULTS, max_fields: '', ...(out.added || {}) }
  out.added.max_fields ??= ''
  return out
}

// the settings block of a typed field, only for its own type (the server forbids others)
function typedSettings(f: any): any {
  switch (f.type) {
    case 'date':
      return { date: compact({ allow_past: !!f.date?.allow_past, max_days_ahead: num(f.date?.max_days_ahead) }) }
    case 'number':
      return { number: compact({ min: num(f.number?.min), max: num(f.number?.max), integer: !!f.number?.integer }) }
    case 'list':
      return {
        items: {
          min_items: num(f.items?.min_items) ?? 1,
          max_items: num(f.items?.max_items) ?? 10,
          item_fields: (f.items?.item_fields || []).map((x: any) => ({
            key: x.key,
            label: text(x.label) ?? {},
            required: x.required !== false,
          })),
        },
      }
    case 'attachment':
      return { attachment: { kinds: [...(f.attachment?.kinds || ['image', 'file'])], max_items: num(f.attachment?.max_items) ?? 1 } }
    case 'receipt': {
      const r = f.receipt || {}
      return {
        receipt: compact({
          amount: num(r.amount),
          amount_field: r.amount_field || undefined,
          quantity_field: r.quantity_field || undefined,
          currency: (r.currency || '').trim().toUpperCase() || undefined,
          recipients: (r.recipients || []).map((x: string) => x.trim()).filter(Boolean),
          max_age_days: num(r.max_age_days) ?? 3,
          tolerance: num(r.tolerance) ?? 0,
        }),
      }
    }
    default:
      return {}
  }
}

// --- human names: the key is made from the name, a person never has to type it ---------

const TRANSLIT: Record<string, string> = {
  а: 'a', б: 'b', в: 'v', г: 'g', ґ: 'g', д: 'd', е: 'e', ё: 'e', є: 'ye', ж: 'zh', з: 'z', и: 'i',
  і: 'i', ї: 'yi', й: 'y', к: 'k', л: 'l', м: 'm', н: 'n', о: 'o', п: 'p', р: 'r', с: 's', т: 't',
  у: 'u', ф: 'f', х: 'h', ц: 'ts', ч: 'ch', ш: 'sh', щ: 'sch', ъ: '', ы: 'y', ь: '', э: 'e', ю: 'yu',
  я: 'ya',
}

// "Номер телефона" → "nomer_telefona"; never a key from `taken`
export function slugKey(value: string, taken: string[] = [], fallback = 'item'): string {
  let out = ''
  for (const ch of (value || '').toLowerCase()) out += TRANSLIT[ch] ?? ch
  out = out
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^[0-9_]+/, '')
    .slice(0, 40)
    .replace(/_+$/, '')
  if (!out) out = fallback
  let key = out
  for (let n = 2; taken.includes(key); n++) key = `${out}_${n}`
  return key
}

// the name of an answer as people see it: its title, or the key when there is none
export function fieldName(f: any, ...languages: (string | null | undefined)[]): string {
  const title = f?.title
  if (typeof title === 'string' && title.trim()) return title
  if (title && typeof title === 'object') {
    for (const l of languages) if (l && (title[l] || '').trim()) return title[l]
    const any = Object.values(title).find((v) => typeof v === 'string' && v.trim())
    if (any) return any as string
  }
  // a key the editor made up says nothing to a person: the caller names such a question
  return f?._auto ? '' : f?.key || ''
}

// a field renamed: everything that points at it by key follows
export function renameField(d: any, from: string, to: string) {
  if (!from || from === to) return
  const swap = (list: string[] | undefined) => (list || []).map((k) => (k === from ? to : k))
  for (const q of d.start?.questions || []) if (q.field === from) q.field = to
  for (const f of d.fields || []) {
    f.ask_after = swap(f.ask_after)
    if (f.receipt?.amount_field === from) f.receipt.amount_field = to
    if (f.receipt?.quantity_field === from) f.receipt.quantity_field = to
  }
  for (const e of d.events_list || []) e.reopen_fields = swap(e.reopen_fields)
  for (const a of d.actions || []) if (a.field === from) a.field = to
}

// --- start: "this is enough" / "only together with another" over weights + threshold ----

export function startThreshold(start: any): number {
  return start?.threshold === '' || start?.threshold == null
    ? Number(start?.questions?.[0]?.weight) || 1
    : Number(start.threshold) || 1
}

export function signEnough(start: any, q: any): boolean {
  return (Number(q.weight) || 1) >= startThreshold(start)
}

// The simple choice writes plain numbers: "enough" = the threshold itself, "together" =
// a half of it (two such signs reach it). The exact numbers stay editable next to it.
export function setSignEnough(start: any, q: any, enough: boolean) {
  let threshold = startThreshold(start)
  if (!enough && threshold < 2) {
    for (const other of start.questions || []) if (other !== q && signEnough(start, other)) other.weight = 2
    threshold = 2
  }
  start.threshold = threshold
  q.weight = enough ? threshold : Math.max(1, Math.ceil(threshold / 2))
}

// index sets of the smallest groups of signs that start the scenario
export function startSets(start: any, max = 4): number[][] {
  const qs = (start?.questions || []) as any[]
  if (!qs.length || qs.length > 12) return []
  const threshold = startThreshold(start)
  const sets: number[][] = []
  for (let mask = 1; mask < 1 << qs.length; mask++) {
    const idx = qs.map((_, i) => i).filter((i) => mask & (1 << i))
    const sum = idx.reduce((s, i) => s + (Number(qs[i].weight) || 1), 0)
    if (sum < threshold) continue
    if (idx.every((i) => sum - (Number(qs[i].weight) || 1) < threshold)) sets.push(idx)
  }
  sets.sort((a, b) => a.length - b.length)
  return sets.slice(0, max)
}

export function startLabel(q: any): string {
  return q.field ? `field: ${q.field}` : q.key || '?'
}

// "Starts e.g. when: A; B + C" — the smallest sets of "yes" answers that reach the threshold.
export function startExamples(start: any, max = 3): string[][] {
  const qs = (start?.questions || []) as any[]
  if (!qs.length || qs.length > 12) return []
  const threshold =
    start.threshold === '' || start.threshold == null ? Number(qs[0]?.weight) || 1 : Number(start.threshold)
  const sets: number[][] = []
  for (let mask = 1; mask < 1 << qs.length; mask++) {
    const idx = qs.map((_, i) => i).filter((i) => mask & (1 << i))
    const sum = idx.reduce((s, i) => s + (Number(qs[i].weight) || 1), 0)
    if (sum < threshold) continue
    // minimal: dropping any one answer falls below the threshold
    if (idx.every((i) => sum - (Number(qs[i].weight) || 1) < threshold)) sets.push(idx)
  }
  sets.sort((a, b) => a.length - b.length)
  return sets.slice(0, max).map((idx) => idx.map((i) => startLabel(qs[i])))
}

export function startTotal(start: any): number {
  return ((start?.questions || []) as any[]).reduce((s, q) => s + (Number(q.weight) || 1), 0)
}

// languages that miss a text, for the red dots on the language tabs
export function missingLanguages(value: unknown, languages: string[], main: string): string[] {
  if (typeof value === 'string') return value.trim() ? languages.filter((l) => l !== main) : languages
  const have = value && typeof value === 'object' ? (value as Record<string, string>) : {}
  return languages.filter((l) => !(have[l] || '').trim())
}

// a start sign in words, for the summaries: «…the client's question…» / names «Service»
export function signLabel(d: any, i: number, lang: string, t: (key: string, params?: any) => string): string {
  const q = d.start?.questions?.[i]
  if (!q) return ''
  if (q.field !== undefined) {
    const f = (d.fields || []).find((x: any) => x.key === q.field)
    return t('editor.start.names_field', { name: (f && fieldName(f, lang, d.language)) || (f ? t('editor.q.untitled') : q.field || '…') })
  }
  const ask = q.ask
  let value = ''
  if (typeof ask === 'string') value = ask
  else if (ask && typeof ask === 'object') value = ask[lang] || ask[d.language] || (Object.values(ask)[0] as string) || ''
  value = value.trim()
  if (!value) return t('editor.start.sign_n', { n: i + 1 })
  return `«${value.length > 80 ? `${value.slice(0, 80)}…` : value}»`
}
