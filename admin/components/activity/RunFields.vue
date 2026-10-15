<template>
  <!-- what a result collected: accepted answers, partial ones, still missing; and the card:
       how the bot asked, whether the question applies (its show_if check), added, closed -->
  <div class="space-y-2">
    <div
      v-for="key in keys"
      :key="key"
      class="rounded-button border px-3 py-2"
      :class="run.fields?.[key] !== undefined
        ? 'border-emerald-200 dark:border-emerald-500/30'
        : run.partial?.[key] !== undefined
          ? 'border-amber-200 dark:border-amber-500/30'
          : 'border-slate-200 dark:border-slate-700'"
    >
      <div class="flex flex-wrap items-center gap-2">
        <span class="code font-medium">{{ key }}</span>
        <span v-if="states?.[key]?.state === 'review'" class="badge-violet">{{ t('results.field.review') }}</span>
        <span v-else-if="run.fields?.[key] !== undefined" class="badge-green">{{ t('results.field.filled') }}</span>
        <span v-else-if="run.partial?.[key] !== undefined" class="badge-amber">{{ t('results.field.partial') }}</span>
        <span v-else-if="!(run.hidden || []).includes(key)" class="badge-gray">{{ t('results.field.missing') }}</span>
        <span v-if="(run.hidden || []).includes(key)" class="badge-gray">{{ t('results.field.hidden') }}</span>
        <span v-if="card(key).closed" class="badge-red">{{ t('results.field.closed') }}</span>
        <span v-if="card(key).added_at" class="badge-blue">{{ t('results.field.added', { at: f.dateTime(card(key).added_at) }) }}</span>
        <span v-if="states?.[key]?.needs_recheck" class="badge-blue">{{ t('results.field.recheck') }}</span>
        <span v-if="states?.[key]?.stuck" class="badge-red">{{ t('results.field.stuck') }}</span>
        <span v-if="run.asking === key" class="badge-violet">{{ t('results.field.asking') }}</span>
      </div>
      <p v-if="question(key)" class="mt-1 text-xs muted">{{ t('results.field.question') }}: {{ question(key) }}</p>
      <p v-if="card(key).asked" class="mt-1 text-xs muted whitespace-pre-line break-words">{{ t('results.field.asked') }}: «{{ card(key).asked }}»</p>
      <p v-if="valueOf(key)" class="mt-1 text-sm whitespace-pre-line break-words">{{ valueOf(key) }}</p>
      <div v-if="card(key).show" class="mt-1.5 rounded-button bg-slate-50 dark:bg-slate-900/40 px-2.5 py-1.5 text-xs space-y-0.5">
        <p class="font-medium">
          {{ t(card(key).show.hidden ? 'results.field.show_no' : 'results.field.show_yes') }}
          <template v-if="card(key).show.unknown"> · {{ t('results.field.show_unknown') }}</template>
          <span class="muted"> · {{ f.dateTime(card(key).show.at) }}</span>
        </p>
        <p v-for="(answer, sign) in card(key).show.answers || {}" :key="sign" class="muted">
          {{ signText(key, String(sign)) }} — {{ t(answer === true ? 'results.field.yes' : answer === false ? 'results.field.no' : 'results.field.unsure') }}
        </p>
      </div>
      <p v-if="normOf(key)" class="mt-0.5 text-xs muted">
        {{ t('results.field.normalized') }}: <span class="code break-all">{{ normOf(key) }}</span>
      </p>
      <p v-if="states?.[key]?.issue" class="mt-1 text-xs text-rose-600">
        {{ t(`field_issue.${states[key].issue}`, issueParams(states[key].issue_params)) }}
      </p>
      <p v-if="states?.[key]?.failed_filters?.length" class="mt-1 text-xs muted">
        {{ t('results.field.failed_filters') }}: <span class="code">{{ states[key].failed_filters.join(', ') }}</span>
      </p>
    </div>
    <p v-if="run.broken_definition" class="alert-amber">{{ t('results.broken_definition') }}</p>
  </div>
</template>

<script setup lang="ts">
import { pickText } from '~/utils/texts'

const props = defineProps<{ run: any; states?: Record<string, any> | null }>()
const { t, locale } = useI18n()
const f = useFormat()

// definition order: accepted and partial keys come back as objects, missing as a list
const keys = computed(() => {
  const seen = new Set<string>()
  const out: string[] = []
  for (const k of [
    ...(props.run.order || []),
    ...Object.keys(props.states || {}),
    ...Object.keys(props.run.fields || {}),
    ...Object.keys(props.run.partial || {}),
    ...(props.run.missing || []),
    ...(props.run.hidden || []),
  ]) {
    if (!seen.has(k)) {
      seen.add(k)
      out.push(k)
    }
  }
  return out
})

// the machine value of a typed field (ISO date, E.164 phone, list items…), when it says
// more than the client's words
function normOf(key: string): string | null {
  const norm = props.run.normalized?.[key] ?? props.states?.[key]?.norm
  if (norm == null || norm === '') return null
  const text = typeof norm === 'string' ? norm : JSON.stringify(norm)
  return text === valueOf(key) ? null : text
}

function issueParams(params: Record<string, any> | null | undefined) {
  const out: Record<string, any> = {}
  for (const [k, v] of Object.entries(params || {})) out[k] = Array.isArray(v) ? v.join(', ') : v
  return out
}

// how the bot asked, the show_if check, added / closed: run.card, or the raw state
function card(key: string): Record<string, any> {
  const row = props.run.card?.[key]
  if (row) return row
  const fs = props.states?.[key] || {}
  return { asked: fs.asked, show: fs.show, closed: fs.closed }
}

function question(key: string): string {
  return pickText(props.run.questions?.[key], locale.value)
}

function signText(key: string, sign: string): string {
  const q = (props.run.show_if?.[key]?.questions || []).find((x: any) => x.key === sign)
  return pickText(q?.ask, locale.value) || sign
}

function valueOf(key: string) {
  return props.run.fields?.[key] ?? props.run.partial?.[key] ?? null
}
</script>
