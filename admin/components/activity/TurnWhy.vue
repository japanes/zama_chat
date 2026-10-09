<template>
  <div>
    <!-- one answer of the bot in a person's words: what it understood, what it looked up,
         what it decided. The technical trace is one tap away, for those who need it. -->
    <p v-if="failed" class="alert-red break-words" role="alert">{{ failed }}</p>

    <!-- a turn that failed decided nothing: only the reason is said -->
    <ol v-else class="space-y-3.5">
      <li v-for="(s, i) in steps" :key="s.key" class="flex gap-3">
        <span class="grid place-items-center w-6 h-6 shrink-0 rounded-full bg-[#e6e9f2] dark:bg-slate-700 text-[13px] font-semibold text-slate-900 dark:text-white">{{ i + 1 }}</span>
        <div class="min-w-0 flex-1">
          <p class="font-semibold text-slate-900 dark:text-white">{{ s.title }}</p>
          <ul class="mt-0.5 space-y-1 text-[15px] text-slate-700 dark:text-slate-200">
            <li v-for="(line, n) in s.lines" :key="n" class="break-words" :class="line.quiet ? 'muted' : ''">{{ line.text }}</li>
          </ul>
        </div>
      </li>
    </ol>

    <p class="mt-3.5 text-sm muted">{{ t('why.took', { time: seconds, cost }) }}</p>

    <details class="mt-3 rounded-button border border-slate-200 dark:border-slate-700">
      <summary class="px-3.5 py-2.5 cursor-pointer text-sm font-medium text-slate-700 dark:text-slate-200">{{ t('why.tech') }}</summary>
      <dl class="px-3.5 pb-3 space-y-1.5 text-sm">
        <div v-for="row in tech" :key="row.label" class="flex gap-3">
          <dt class="w-40 shrink-0 muted">{{ row.label }}</dt>
          <dd class="min-w-0 break-words text-slate-900 dark:text-white">{{ row.value }}</dd>
        </div>
        <button type="button" class="link pt-1" :aria-expanded="inline ? traceOpen : undefined" @click="traceOpen = !traceOpen">{{ t('why.full') }}</button>
      </dl>
      <div v-if="inline && traceOpen" class="border-t border-slate-200 dark:border-slate-700 p-3.5">
        <TurnTrace :detail="detail" />
      </div>
    </details>

    <UiModal v-if="!inline" :open="traceOpen" :title="t('chat.turn')" size="xl" @close="traceOpen = false">
      <TurnTrace v-if="traceOpen" :detail="detail" />
    </UiModal>
  </div>
</template>

<script setup lang="ts">
import { pickText } from '~/utils/texts'

interface Line {
  text: string
  quiet?: boolean
}

// detail: a turn as the admin API gives it (turn, explain, decisions, llm_calls);
// runs: the conversation's runs — the titles of scenarios and questions come from them;
// inline: the whole trace opens in place (when this is already inside a dialog)
const props = defineProps<{ detail: any; runs?: any[]; inline?: boolean }>()
const { t, te, locale } = useI18n()
const f = useFormat()
const { codeText } = useErrorText()
const traceOpen = ref(false)

const turn = computed(() => props.detail.turn || {})
const explain = computed(() => props.detail.explain || {})
// why the turn failed, in words; '' for a turn that went through
const failed = computed(() => {
  const failure = props.detail.failure
  if (!failure) return turn.value.error ? `${t('why.failed')} ${turn.value.error}` : ''
  // «the bot could not answer: …» is already the text of this code
  return failure.code === 'turn.failed' ? codeText(failure) : `${t('why.failed')} ${codeText(failure)}`
})

function runOf(scenario: string): any {
  return (props.runs || []).find((r) => r.scenario_key === scenario) || null
}
function scenarioName(key: string): string {
  const r = runOf(key)
  return pickText(r?.scenario_title, locale.value, r?.reply_language) || key
}
// a question without a name is called by its text: a key says nothing to a person
function fieldName(scenario: string, field: string): string {
  const r = runOf(scenario)
  const question = pickText(r?.questions?.[field], locale.value, r?.reply_language).replace(/[\s?:.]+$/, '')
  return pickText(r?.titles?.[field], locale.value, r?.reply_language) || question || field
}
// a phone found by code or a file: the trace has no value, the run has
function fieldValue(line: any): string {
  if (line.value) return line.value
  const r = runOf(line.scenario)
  const value = r?.fields?.[line.field] ?? r?.partial?.[line.field]
  return value === undefined || value === null || value === '' ? t('why.understood.taken') : String(value)
}
function issueText(line: any): string {
  const key = `field_issue.${line.issue}`
  if (!te(key)) return t('why.understood.issue_other')
  const params: Record<string, any> = {}
  for (const [k, v] of Object.entries(line.params || {})) params[k] = Array.isArray(v) ? v.join(', ') : v
  return t(key, params)
}
function names(list: any[], more: number): string {
  const shown = list.map((i) => (i.price ? `${i.title} — ${i.price}${i.currency ? ` ${i.currency}` : ''}` : i.title)).filter(Boolean)
  const text = shown.join('; ')
  return more > 0 ? `${text}; ${t('why.found.more', { n: more })}` : text
}

function understoodLine(line: any): Line {
  const scenario = scenarioName(line.scenario || '')
  switch (line.what) {
    case 'meaning':
      return { text: t('why.understood.meaning', { text: line.text }) }
    case 'answer':
      return { text: t('why.understood.answer', { field: fieldName(line.scenario, line.field), value: fieldValue(line) }) }
    case 'not_accepted':
      return { text: t('why.understood.not_accepted', { field: fieldName(line.scenario, line.field), issue: issueText(line) }) }
    case 'started':
    case 'cancelled':
    case 'paused':
      return { text: t(`why.understood.${line.what}`, { scenario }) }
    case 'plain':
      return { text: t('why.understood.plain'), quiet: true }
    default:
      return { text: te(`why.understood.${line.what}`) ? t(`why.understood.${line.what}`) : String(line.what) }
  }
}
function foundLine(line: any): Line {
  if (line.what === 'nothing') return { text: t(`why.found.nothing_${line.where === 'catalog' || line.where === 'knowledge' ? line.where : 'both'}`) }
  return { text: t(`why.found.${line.what}`, { list: names(line.items || [], line.more || 0) }) }
}
function decidedLine(line: any): Line {
  const key = `why.decided.${line.what}`
  if (!te(key)) return { text: String(line.what) }
  return {
    text: t(key, {
      scenario: scenarioName(line.scenario || ''),
      field: line.field ? fieldName(line.scenario, line.field) : '',
      n: line.n ?? '',
    }),
  }
}

const steps = computed(() => {
  const found = ((explain.value.found || []) as any[]).map(foundLine)
  return [
    { key: 'understood', title: t('why.understood.title'), lines: ((explain.value.understood || []) as any[]).map(understoodLine) },
    { key: 'found', title: t('why.found.title'), lines: found.length ? found : [{ text: t('why.found.none'), quiet: true }] },
    { key: 'decided', title: t('why.decided.title'), lines: ((explain.value.decided || []) as any[]).map(decidedLine) },
  ]
})

const seconds = computed(() => {
  const ms = Number(turn.value.duration_ms)
  return Number.isFinite(ms) && ms > 0 ? f.num(Math.max(1, Math.round(ms / 1000))) : '—'
})
const cost = computed(() => {
  const v = Number(turn.value.cost)
  if (!Number.isFinite(v) || v <= 0) return t('why.cost_none')
  return v < 0.01 ? t('why.cost_cent') : f.money(v)
})

const tech = computed(() => {
  const decisions = ((props.detail.decisions || []) as any[]).filter((d) => d.used !== false)
  const calls = (props.detail.llm_calls || []) as any[]
  const jev = decisions.filter((d) => d.provider === 'jev').length
  const llm = decisions.length - jev
  const lowest = decisions.map((d) => Number(d.confidence)).filter((c) => Number.isFinite(c)).sort((a, b) => a - b)[0]
  const models = [...new Set(calls.map((c) => c.model).filter(Boolean))]
  const rows: { label: string; value: string }[] = []
  if (decisions.length) rows.push({ label: t('why.tech_who'), value: t('why.tech_who_value', { jev, llm }) })
  if (lowest !== undefined) rows.push({ label: t('why.tech_confidence'), value: f.num(lowest, 2) })
  rows.push({ label: t('why.tech_calls'), value: String(calls.length) })
  if (models.length) rows.push({ label: t('why.tech_models'), value: models.join(', ') })
  if (turn.value.client_language)
    rows.push({ label: t('why.tech_languages'), value: t('trace.languages', { client: turn.value.client_language, reply: turn.value.reply_language || '—' }) })
  return rows
})
</script>
