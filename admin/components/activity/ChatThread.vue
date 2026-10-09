<template>
  <div class="space-y-4">
    <!-- messages of a conversation: the client on the left, the bot and people on the right;
         under every answer — who gave it. `explainable` adds «why this answer?» to bot
         answers (the parent shows the explanation), `withTrace` opens the raw trace inline. -->
    <div v-for="m in messages" :key="m.id">
      <p v-if="m.role === 'system'" class="text-center text-sm italic text-slate-500 dark:text-slate-400 whitespace-pre-line break-words">
        {{ m.text }}
      </p>
      <div v-else class="flex flex-col" :class="m.role === 'client' ? 'items-start' : 'items-end'">
        <div class="max-w-[88%] md:max-w-[75%] px-4 py-2.5 text-[15px] leading-relaxed" :class="[bubble(m), marked(m) ? 'ring-2 ring-offset-2 ring-[#5b4b9a] ring-offset-slate-50 dark:ring-violet-400 dark:ring-offset-slate-900' : '']">
          <p v-if="m.text" class="whitespace-pre-line break-words">{{ m.text }}</p>
          <div v-if="m.attachments?.length" class="flex flex-wrap gap-1.5" :class="m.text ? 'mt-2' : ''">
            <template v-for="(a, i) in m.attachments" :key="i">
              <a v-if="a.url" :href="a.url" target="_blank" rel="noopener noreferrer" class="badge-gray hover:underline">
                <UiIcon name="paperclip" :size="14" /> {{ attachmentName(a) }}
              </a>
              <span v-else class="badge-gray"><UiIcon name="paperclip" :size="14" /> {{ attachmentName(a) }}</span>
            </template>
          </div>
        </div>
        <div class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-slate-500 dark:text-slate-400" :class="m.role === 'client' ? '' : 'justify-end'">
          <span v-if="m.role !== 'client'">{{ who(m) }}</span>
          <span>{{ f.time(m.created_at) }}</span>
          <StatusBadge v-if="m.role !== 'client' && m.send_status && m.send_status !== 'sent'" kind="send" :value="m.send_status" />
          <span v-if="marked(m)" class="badge-violet">{{ selectedNote }}</span>
          <button
            v-else-if="m.turn_id && m.role === 'bot' && explainable"
            type="button"
            class="link"
            :class="selectedTurn === m.turn_id ? 'underline' : ''"
            @click="emit('explain', m.turn_id)"
          >
            {{ t('chat.why') }}
          </button>
          <button v-if="m.turn_id && m.role !== 'client' && withTrace" type="button" class="link" @click="toggle(m.turn_id)">
            {{ open.has(m.turn_id) ? t('chat.hide_trace') : t('chat.show_trace') }}
          </button>
        </div>
        <p v-if="m.send_error" class="mt-1 text-[13px] text-[#a61b34] dark:text-rose-300 break-words">{{ codeText({ code: m.send_error }) }}</p>
      </div>
      <div v-if="m.turn_id && open.has(m.turn_id) && m.role !== 'client'" class="mt-2">
        <TurnPanel :turn-id="m.turn_id" :preloaded="preloaded?.turn?.id === m.turn_id ? preloaded : undefined" />
      </div>
    </div>
    <p v-if="!messages.length" class="py-10 text-center muted">{{ t('chat.empty') }}</p>
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{
  messages: any[]
  withTrace?: boolean
  explainable?: boolean
  // the turn whose explanation is open now
  selectedTurn?: string | null
  // said under the answer whose explanation is open (and the answer gets a ring)
  selectedNote?: string
  preloaded?: any
  openTurn?: string | null
}>()
const emit = defineEmits<{ explain: [turnId: string] }>()
const { t } = useI18n()
const f = useFormat()
const { codeText } = useErrorText()
const open = reactive(new Set<string>())

watch(
  () => props.openTurn,
  (id) => {
    if (id) open.add(id)
  },
  { immediate: true },
)

function marked(m: any): boolean {
  return !!props.selectedNote && m.role === 'bot' && !!m.turn_id && props.selectedTurn === m.turn_id
}

function toggle(id: string) {
  if (open.has(id)) open.delete(id)
  else open.add(id)
}

// who gave the answer, in plain words
function who(m: any): string {
  if (m.role === 'bot') return t(m.meta?.source === 'action' ? 'chat.by.action' : 'chat.by.bot')
  if (m.meta?.source === 'instagram_app') return t('chat.by.app')
  if (m.meta?.variant !== undefined && m.meta?.variant !== null) return t(m.meta.edited ? 'chat.by.variant_edited' : 'chat.by.variant')
  return t('chat.by.person')
}

function attachmentName(a: any): string {
  return a.name || a.type || t('chat.attachment')
}

function bubble(m: any) {
  if (m.role === 'client')
    return 'rounded-[14px_14px_14px_4px] bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-slate-900 dark:text-slate-100'
  if (m.role === 'operator')
    return 'rounded-[14px_14px_4px_14px] bg-[#e8f2fc] dark:bg-sky-500/15 border border-[#bcd9f5] dark:border-sky-500/30 text-slate-900 dark:text-slate-100'
  return 'rounded-[14px_14px_4px_14px] bg-primary-dark text-white'
}
</script>
