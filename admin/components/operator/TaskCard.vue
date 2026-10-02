<template>
  <!-- one open operator task: choose a reply variant (or write your own) / a handed-over conversation -->
  <div class="rounded-card border p-4 space-y-3" :class="task.kind === 'handover' ? 'border-rose-200 dark:border-rose-900/60' : 'border-violet-200 dark:border-violet-900/60'">
    <div class="flex flex-wrap items-center gap-2">
      <span :class="task.kind === 'handover' ? 'badge-red' : 'badge-violet'">{{ t(`operator.kind.${task.kind}`) }}</span>
      <template v-if="withContact && task.contact">
        <NuxtLink :to="`${base}/conversations/${task.conversation_id}`" class="font-medium hover:text-primary">
          {{ task.contact.name || task.contact.username || task.contact.external_id }}
        </NuxtLink>
        <span class="text-xs muted">{{ task.channel?.title }}</span>
      </template>
      <span class="ml-auto text-xs muted" :title="f.dateTime(task.created_at)">{{ f.relative(task.created_at) }}</span>
    </div>

    <p v-if="clientText" class="rounded-button bg-slate-50 dark:bg-slate-900/40 px-3 py-2 text-sm whitespace-pre-line break-words">{{ clientText }}</p>

    <template v-if="task.kind === 'choose_reply'">
      <p v-if="task.version > seen" class="alert-amber flex flex-wrap items-center gap-2">
        <span class="flex-1">{{ t('operator.rebuilt') }}</span>
        <button type="button" class="btn-sm btn-secondary" @click="acceptNew">{{ t('operator.show_new') }}</button>
      </p>
      <div class="space-y-2">
        <label
          v-for="(v, i) in variants"
          :key="i"
          class="flex items-start gap-2.5 rounded-button border px-3 py-2 text-sm cursor-pointer"
          :class="chosen === i ? 'border-primary bg-primary/5' : 'border-slate-200 dark:border-slate-700 hover:border-primary/50'"
        >
          <input type="radio" class="mt-0.5" :checked="chosen === i" :disabled="!canAct" @change="choose(i)" />
          <span class="whitespace-pre-line break-words">{{ v }}</span>
        </label>
      </div>
      <div v-if="canAct">
        <label class="label">{{ t('operator.reply') }}</label>
        <textarea v-model="text" rows="3" class="textarea" :placeholder="t('operator.reply_placeholder')" @input="edited = true" />
        <p v-if="edited && chosen !== null" class="hint">{{ t('operator.edited_hint') }}</p>
      </div>
      <div v-if="canAct" class="flex flex-wrap justify-end gap-2">
        <button type="button" class="btn-secondary btn-sm" :disabled="busy" @click="resolve('dismiss')">{{ t('operator.dismiss') }}</button>
        <button type="button" class="btn-primary btn-sm" :disabled="busy || !text.trim() || task.version > seen" @click="resolve('send')">
          <UiIcon name="send" :size="14" /> {{ t('operator.send') }}
        </button>
      </div>
    </template>

    <template v-else>
      <p class="text-sm muted">{{ t('operator.handover_hint') }}</p>
      <div v-if="canAct" class="flex flex-wrap justify-end gap-2">
        <button type="button" class="btn-secondary btn-sm" :disabled="busy" @click="resolve('dismiss')">{{ t('operator.dismiss') }}</button>
        <NuxtLink v-if="withContact" :to="`${base}/conversations/${task.conversation_id}`" class="btn-secondary btn-sm">{{ t('operator.open_chat') }}</NuxtLink>
        <button type="button" class="btn-primary btn-sm" :disabled="busy" @click="resolve('release')">{{ t('operator.release') }}</button>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{ task: any; withContact?: boolean; canAct?: boolean }>()
const emit = defineEmits<{ resolved: [any] }>()
const { t } = useI18n()
const f = useFormat()
const api = useApi()
const { base, path } = useTenant()
const { busy, run } = useAction()
const { confirm } = useConfirm()

// the variants the operator is looking at: a poll with new variants does not replace
// them under the cursor — the operator presses "show new" first
const seen = ref<number>(props.task.version)
const variants = ref<string[]>([...(props.task.payload?.variants || [])])
const chosen = ref<number | null>(null)
const text = ref('')
const edited = ref(false)

const clientText = computed(() => {
  const p = props.task.payload || {}
  if (props.task.kind === 'handover') return p.last_text || p.input_text || props.task.last_client_text || ''
  return p.input_text || props.task.last_client_text || ''
})

function choose(i: number) {
  chosen.value = i
  text.value = variants.value[i] || ''
  edited.value = false
}

function acceptNew() {
  seen.value = props.task.version
  variants.value = [...(props.task.payload?.variants || [])]
  // an own text stays; a picked variant is gone with the old list
  chosen.value = null
  if (!edited.value) text.value = ''
}

async function resolve(action: 'send' | 'dismiss' | 'release') {
  if (action === 'dismiss' && !(await confirm({ message: t('operator.dismiss_confirm') }))) return
  const body: Record<string, unknown> = { action, version: seen.value }
  if (action === 'send') {
    body.variant = chosen.value
    body.text = text.value.trim()
  }
  const out = await run(
    () => api<any>(path(`/operator/tasks/${props.task.id}/resolve`), { method: 'POST', body }),
    t(action === 'send' ? 'operator.sent' : 'common.saved'),
  )
  // also on an error (outdated, closed): the parent reloads and shows the new state
  emit('resolved', out ?? null)
}
</script>
