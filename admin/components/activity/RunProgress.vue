<template>
  <div>
    <!-- what the bot has already learned in a scenario, as a checklist in plain words -->
    <div class="flex items-start justify-between gap-3">
      <div class="min-w-0">
        <p class="font-semibold text-slate-900 dark:text-white truncate">{{ title }}</p>
        <p class="text-sm muted">{{ t('progress.collected', { done, total: rows.length }) }}</p>
      </div>
      <StatusBadge kind="run" :value="run.status" />
    </div>
    <div class="mt-2 h-1.5 rounded-full bg-slate-100 dark:bg-slate-700 overflow-hidden">
      <div class="h-full rounded-full bg-[#17663f] dark:bg-emerald-400 transition-[width]" :style="{ width: `${percent}%` }" />
    </div>

    <ul class="mt-3 space-y-2.5">
      <li v-for="row in rows" :key="row.key" class="flex items-start gap-2.5">
        <span
          class="mt-0.5 grid place-items-center w-5 h-5 shrink-0 rounded-full"
          :class="row.state === 'done'
            ? 'bg-[#e4f3ea] text-[#17663f] dark:bg-emerald-500/15 dark:text-emerald-300'
            : row.state === 'partial'
              ? 'bg-[#fdf0d2] text-[#7a4b00] dark:bg-amber-500/15 dark:text-amber-300'
              : 'border-2 border-slate-300 dark:border-slate-600'"
        >
          <UiIcon v-if="row.state === 'done'" name="check" :size="12" :stroke="2.5" />
          <span v-else-if="row.state === 'partial'" class="w-1.5 h-1.5 rounded-full bg-current" />
        </span>
        <div class="min-w-0 flex-1">
          <p class="text-sm" :class="row.state === 'todo' ? 'text-slate-500 dark:text-slate-400' : 'font-medium text-slate-900 dark:text-white'">
            {{ row.label }}
            <span v-if="row.asking" class="badge-violet ml-1">{{ t('progress.asking') }}</span>
            <span v-if="row.review" class="badge-amber ml-1">{{ t('progress.review') }}</span>
            <span v-if="row.stuck" class="badge-red ml-1">{{ t('progress.stuck') }}</span>
          </p>
          <p v-if="row.value" class="text-sm text-slate-600 dark:text-slate-300 whitespace-pre-line break-words">{{ row.value }}</p>
          <p v-if="row.state === 'partial'" class="text-[13px] text-[#7a4b00] dark:text-amber-300">{{ row.issue || t('progress.partial') }}</p>
          <p v-else-if="row.state === 'todo' && !row.asking" class="text-[13px] text-slate-500 dark:text-slate-400">{{ t('progress.todo') }}</p>
        </div>
      </li>
    </ul>
    <p v-if="run.broken_definition" class="alert-amber mt-3">{{ t('results.broken_definition') }}</p>
  </div>
</template>

<script setup lang="ts">
import { pickText } from '~/utils/texts'

// run: a run as the admin API gives it (fields / partial / missing / order / titles);
// states: the per-field details of a sandbox run, when there are any
const props = defineProps<{ run: any; states?: Record<string, any> | null }>()
const { t, te, locale } = useI18n()

const title = computed(() => pickText(props.run.scenario_title, locale.value, props.run.reply_language) || props.run.scenario_key)

interface Row {
  key: string
  label: string
  value: string | null
  state: 'done' | 'partial' | 'todo'
  asking: boolean
  review: boolean
  stuck: boolean
  issue: string | null
}

const rows = computed<Row[]>(() => {
  const run = props.run
  const order: string[] = run.order?.length
    ? run.order
    : [...new Set([...Object.keys(run.fields || {}), ...Object.keys(run.partial || {}), ...(run.missing || [])])]
  return order.map((key) => {
    const st = props.states?.[key]
    const filled = run.fields?.[key] !== undefined
    const partial = !filled && run.partial?.[key] !== undefined
    const issueKey = st?.issue ? `field_issue.${st.issue}` : ''
    return {
      key,
      // a question without a name is called by its text: a key says nothing to a person
      label: pickText(run.titles?.[key], locale.value, run.reply_language) || pickText(run.questions?.[key], locale.value, run.reply_language) || key,
      value: run.fields?.[key] ?? run.partial?.[key] ?? null,
      state: filled ? 'done' : partial ? 'partial' : 'todo',
      asking: run.asking === key,
      review: st?.state === 'review',
      stuck: !!st?.stuck,
      issue: issueKey && te(issueKey) ? t(issueKey, flat(st.issue_params)) : null,
    }
  })
})
const done = computed(() => rows.value.filter((r) => r.state === 'done').length)
const percent = computed(() => (rows.value.length ? Math.round((done.value / rows.value.length) * 100) : 0))

function flat(params: Record<string, any> | null | undefined) {
  const out: Record<string, any> = {}
  for (const [k, v] of Object.entries(params || {})) out[k] = Array.isArray(v) ? v.join(', ') : v
  return out
}
</script>
