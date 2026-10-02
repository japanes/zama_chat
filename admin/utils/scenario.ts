// Scenario definition helpers for the editor. The server is the judge (POST …/check);
// here only what the form needs: a blank scenario, cleaning before sending, hints.

export type LText = string | Record<string, string>

export const FIELD_TYPES = ['text', 'choice', 'yes_no'] as const
export const COMPLETIONS = ['all_fields', 'explicit_yes', 'external_event', 'receipt'] as const
export const ACTION_EVENTS = [
  'started',
  'field_filled',
  'field_stuck',
  'all_fields',
  'confirmed',
  'completed',
  'cancelled',
  'abandoned',
  'silence',
] as const
export const ACTIONS = ['http_post', 'notify_telegram', 'notify_email', 'message_client', 'handover'] as const
export const NOTIFY_ACTIONS = ['notify_telegram', 'notify_email']
export const KEY_RE = /^[a-z][a-z0-9]*(_[a-z0-9]+)*$/

export function newField(n: number): any {
  return { key: `field_${n}`, type: 'text', question: {}, options: [], filters: [], ask_after: [] }
}

export function newDefinition(language: string): any {
  return {
    key: '',
    title: {},
    language,
    start: { questions: [{ key: 'wants_it', ask: {}, weight: 1 }], threshold: 1 },
    fields: [newField(1)],
    completion: 'all_fields',
    resume: { mode: 'always' },
    cancel: {},
    actions: [],
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
        question: text(f.question) ?? {},
        description: text(f.description),
        options:
          f.type === 'choice'
            ? (f.options || []).map((o: any) => ({ key: o.key, label: text(o.label) ?? {} }))
            : undefined,
        filters: (f.filters || []).length
          ? f.filters.map((x: any) => compact({ key: x.key, ask: text(x.ask) ?? {}, yes: text(x.yes), no: text(x.no) }))
          : undefined,
        ask_after: (f.ask_after || []).length ? [...f.ask_after] : undefined,
      }),
    ),
    completion: d.completion || 'all_fields',
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
      }),
    ),
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
  }
  out.completion ||= 'all_fields'
  out.resume ||= { mode: 'always' }
  out.cancel ||= {}
  out.actions ||= []
  for (const a of out.actions) {
    a.do ||= 'http_post'
    a.params ||= {}
  }
  return out
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
