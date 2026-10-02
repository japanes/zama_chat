<template>
  <span :class="cls">{{ label }}</span>
</template>

<script setup lang="ts">
// Status of a result, delivery, turn, message or channel, colored and translated.
// Labels: status.<kind>.<value> in common.json.
const props = defineProps<{ kind: 'run' | 'delivery' | 'turn' | 'send' | 'channel' | 'index'; value?: string | null }>()
const { t, te } = useI18n()

const COLORS: Record<string, string> = {
  // runs
  focus: 'badge-blue',
  paused: 'badge-gray',
  waiting: 'badge-violet',
  completed: 'badge-green',
  cancelled: 'badge-gray',
  abandoned: 'badge-amber',
  // deliveries, messages
  pending: 'badge-amber',
  sent: 'badge-green',
  failed: 'badge-red',
  dropped: 'badge-gray',
  // turns
  running: 'badge-blue',
  done: 'badge-green',
  superseded: 'badge-gray',
  // channels
  connected: 'badge-green',
  expiring: 'badge-amber',
  error: 'badge-red',
  disabled: 'badge-gray',
  // catalog items, knowledge sources
  indexed: 'badge-green',
}

const cls = computed(() => COLORS[props.value || ''] || 'badge-gray')
const label = computed(() => {
  const key = `status.${props.kind}.${props.value}`
  return props.value && te(key) ? t(key) : props.value || '—'
})
</script>
