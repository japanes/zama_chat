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
        <span v-if="run.fields?.[key] !== undefined" class="badge-green">{{ t('results.field.filled') }}</span>
        <span v-else-if="run.partial?.[key] !== undefined" class="badge-amber">{{ t('results.field.partial') }}</span>
        <span v-else class="badge-gray">{{ t('results.field.missing') }}</span>
        <span v-if="states?.[key]?.needs_recheck" class="badge-blue">{{ t('results.field.recheck') }}</span>
        <span v-if="states?.[key]?.stuck" class="badge-red">{{ t('results.field.stuck') }}</span>
        <span v-if="run.asking === key" class="badge-violet">{{ t('results.field.asking') }}</span>
      </div>
      <p v-if="valueOf(key)" class="mt-1 text-sm whitespace-pre-line break-words">{{ valueOf(key) }}</p>
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

function valueOf(key: string) {
  return props.run.fields?.[key] ?? props.run.partial?.[key] ?? null
}
</script>
