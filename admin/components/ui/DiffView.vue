<template>
  <div class="pre max-h-[32rem] p-0">
    <div
      v-for="(line, i) in lines"
      :key="i"
      class="px-3"
      :class="{
        'bg-emerald-50 text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300': line.kind === '+',
        'bg-rose-50 text-rose-800 dark:bg-rose-500/10 dark:text-rose-300': line.kind === '-',
      }"
    ><span class="select-none opacity-50 mr-2">{{ line.kind }}</span>{{ line.text || ' ' }}</div>
    <div v-if="!changed" class="px-3 py-2 muted">{{ t('common.no_changes') }}</div>
  </div>
</template>

<script setup lang="ts">
import { diffLines } from 'diff'

const props = defineProps<{ before: string; after: string }>()
const { t } = useI18n()

const lines = computed(() => {
  const out: { kind: '+' | '-' | ' '; text: string }[] = []
  for (const part of diffLines(props.before || '', props.after || '')) {
    const kind = part.added ? '+' : part.removed ? '-' : ' '
    const rows = part.value.replace(/\n$/, '').split('\n')
    for (const text of rows) out.push({ kind, text })
  }
  return out
})
const changed = computed(() => lines.value.some((l) => l.kind !== ' '))
</script>
