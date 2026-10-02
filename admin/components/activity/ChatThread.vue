<template>
  <!-- messages of a conversation; a bot answer opens the trace of its turn -->
  <div class="space-y-3">
    <div v-for="m in messages" :key="m.id">
      <div class="flex" :class="m.role === 'client' ? 'justify-start' : m.role === 'system' ? 'justify-center' : 'justify-end'">
        <div
          class="max-w-[85%] md:max-w-[70%] rounded-card px-3.5 py-2.5 text-sm"
          :class="bubble(m.role)"
        >
          <p class="whitespace-pre-line break-words">{{ m.text }}</p>
          <div v-if="m.attachments?.length" class="mt-1.5 flex flex-wrap gap-1">
            <span v-for="(a, i) in m.attachments" :key="i" class="badge-gray">{{ a.type || t('chat.attachment') }}</span>
          </div>
          <div class="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px] opacity-70">
            <span v-if="m.role === 'operator'">{{ t('chat.role.operator') }} ·</span>
            <span>{{ f.time(m.created_at) }}</span>
            <StatusBadge v-if="m.role === 'bot' && m.send_status && m.send_status !== 'sent'" kind="send" :value="m.send_status" />
            <button
              v-if="m.turn_id && m.role !== 'client' && withTrace"
              type="button"
              class="underline decoration-dotted hover:opacity-100"
              @click="toggle(m.turn_id)"
            >
              {{ open.has(m.turn_id) ? t('chat.hide_trace') : t('chat.show_trace') }}
            </button>
          </div>
          <p v-if="m.send_error" class="mt-1 text-xs text-rose-600 break-words">{{ m.send_error }}</p>
        </div>
      </div>
      <div v-if="m.turn_id && open.has(m.turn_id) && m.role !== 'client'" class="mt-2">
        <TurnPanel :turn-id="m.turn_id" :preloaded="preloaded?.turn?.id === m.turn_id ? preloaded : undefined" />
      </div>
    </div>
    <p v-if="!messages.length" class="py-8 text-center text-sm muted">{{ t('chat.empty') }}</p>
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{ messages: any[]; withTrace?: boolean; preloaded?: any; openTurn?: string | null }>()
const { t } = useI18n()
const f = useFormat()
const open = reactive(new Set<string>())

watch(
  () => props.openTurn,
  (id) => {
    if (id) open.add(id)
  },
  { immediate: true },
)

function toggle(id: string) {
  if (open.has(id)) open.delete(id)
  else open.add(id)
}

function bubble(role: string) {
  if (role === 'client') return 'bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-slate-800 dark:text-slate-100'
  if (role === 'operator') return 'bg-secondary/10 text-slate-800 dark:text-slate-100 border border-secondary/30'
  if (role === 'system') return 'bg-transparent text-slate-500 italic'
  return 'bg-primary text-white'
}
</script>
