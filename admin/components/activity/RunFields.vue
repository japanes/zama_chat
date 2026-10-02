<template>
  <!-- what a result collected: accepted answers, partial ones, still missing -->
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
        <span v-else class="badge-gray">{{ t('results.field.missing') }}</span>
        <span v-if="states?.[key]?.needs_recheck" class="badge-blue">{{ t('results.field.recheck') }}</span>
        <span v-if="states?.[key]?.stuck" class="badge-red">{{ t('results.field.stuck') }}</span>
        <span v-if="run.asking === key" class="badge-violet">{{ t('results.field.asking') }}</span>
      </div>
      <p v-if="valueOf(key)" class="mt-1 text-sm whitespace-pre-line break-words">{{ valueOf(key) }}</p>
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
const props = defineProps<{ run: any; states?: Record<string, any> | null }>()
const { t } = useI18n()

// definition order: accepted and partial keys come back as objects, missing as a list
const keys = computed(() => {
  const seen = new Set<string>()
  const out: string[] = []
  for (const k of [
    ...Object.keys(props.states || {}),
    ...Object.keys(props.run.fields || {}),
    ...Object.keys(props.run.partial || {}),
    ...(props.run.missing || []),
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

function valueOf(key: string) {
  return props.run.fields?.[key] ?? props.run.partial?.[key] ?? null
}
</script>
