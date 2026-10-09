<template>
  <!-- one open operator task: choose a reply variant (or write your own) / a handed-over
       conversation / a scenario action to approve / a payment receipt to check -->
  <div class="rounded-card border p-4 space-y-3" :class="LOOK[task.kind]?.border || LOOK.choose_reply.border">
    <div class="flex flex-wrap items-center gap-2">
      <span :class="LOOK[task.kind]?.badge || 'badge-gray'">{{ t(`operator.kind.${task.kind}`) }}</span>
      <template v-if="withContact && task.contact">
        <NuxtLink :to="`${base}/inbox/${task.conversation_id}`" class="font-medium hover:text-primary">
          {{ task.contact.name || task.contact.username || task.contact.external_id }}
        </NuxtLink>
        <span class="text-xs muted">{{ task.channel?.title }}</span>
      </template>
      <span class="ml-auto text-xs muted" :title="f.dateTime(task.created_at)">{{ f.relative(task.created_at) }}</span>
    </div>

    <p v-if="clientText && !isRunTask" class="rounded-button bg-slate-50 dark:bg-slate-900/40 px-3 py-2 text-sm whitespace-pre-line break-words">{{ clientText }}</p>

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

    <template v-else-if="task.kind === 'approve_action'">
      <p class="text-sm">
        {{ t('operator.approve_text', { action: t(`scenarios.action.${p.do}`, p.do), event: t(`scenarios.event.${p.event}`, p.event) }) }}
        <span v-if="p.endpoint" class="code text-xs">→ {{ p.endpoint }}</span>
      </p>
      <p class="text-xs muted">{{ t('operator.scenario_label') }}: {{ p.scenario_title || p.scenario }}</p>
      <pre v-if="p.fields" class="rounded-button bg-slate-50 dark:bg-slate-900/40 px-3 py-2 text-xs whitespace-pre-wrap break-words">{{ p.fields }}</pre>
      <div v-if="canAct" class="flex flex-wrap justify-end gap-2">
        <button type="button" class="btn-secondary btn-sm" :disabled="busy" @click="resolve('dismiss')">{{ t('operator.dismiss') }}</button>
        <button type="button" class="btn-secondary btn-sm text-rose-600" :disabled="busy" @click="resolve('reject')">{{ t('operator.reject') }}</button>
        <button type="button" class="btn-primary btn-sm" :disabled="busy" @click="resolve('approve')">{{ t('operator.approve') }}</button>
      </div>
    </template>

    <template v-else-if="task.kind === 'review_receipt'">
      <div class="grid gap-3 md:grid-cols-[10rem_1fr]">
        <a v-if="p.url" :href="p.url" target="_blank" rel="noopener noreferrer" class="block">
          <img :src="p.url" :alt="t('operator.receipt_image')" class="max-h-56 w-full rounded-button border border-slate-200 dark:border-slate-700 object-contain bg-white" loading="lazy" />
          <span class="text-xs text-primary">{{ t('operator.open_original') }}</span>
        </a>
        <div class="space-y-2 text-sm">
          <p class="text-xs muted">{{ t('operator.scenario_label') }}: {{ p.scenario_title || p.scenario }} · <span class="code">{{ p.field }}</span></p>
          <p v-if="p.review_reason" class="text-xs">{{ t(`operator.review_reason.${p.review_reason}`) }}</p>
          <ReceiptFacts :extracted="p.extracted" :expected="p.expected" />
          <ul v-if="(p.issues || []).length" class="space-y-0.5">
            <li v-for="(x, i) in p.issues" :key="i" class="text-rose-600 text-xs">• {{ t(`receipt_issue.${x.code}`, x.params || {}) }}</li>
          </ul>
        </div>
      </div>
      <pre v-if="p.fields" class="rounded-button bg-slate-50 dark:bg-slate-900/40 px-3 py-2 text-xs whitespace-pre-wrap break-words">{{ p.fields }}</pre>
      <div v-if="canAct">
        <label class="label">{{ t('operator.note') }} <span class="muted font-normal">({{ t('common.optional') }})</span></label>
        <input v-model="note" class="input" maxlength="1000" :placeholder="t('operator.note_placeholder')" />
      </div>
      <div v-if="canAct" class="flex flex-wrap justify-end gap-2">
        <NuxtLink v-if="withContact" :to="`${base}/inbox/${task.conversation_id}`" class="btn-secondary btn-sm">{{ t('operator.open_chat') }}</NuxtLink>
        <button type="button" class="btn-secondary btn-sm text-rose-600" :disabled="busy" @click="resolve('reject')">{{ t('operator.reject_receipt') }}</button>
        <button type="button" class="btn-primary btn-sm" :disabled="busy" @click="resolve('accept')">{{ t('operator.accept_receipt') }}</button>
      </div>
    </template>

    <template v-else>
      <p class="text-sm muted">{{ t('operator.handover_hint') }}</p>
      <div v-if="canAct" class="flex flex-wrap justify-end gap-2">
        <button type="button" class="btn-secondary btn-sm" :disabled="busy" @click="resolve('dismiss')">{{ t('operator.dismiss') }}</button>
        <NuxtLink v-if="withContact" :to="`${base}/inbox/${task.conversation_id}`" class="btn-secondary btn-sm">{{ t('operator.open_chat') }}</NuxtLink>
        <button type="button" class="btn-primary btn-sm" :disabled="busy" @click="resolve('release')">{{ t('operator.release') }}</button>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{ task: any; withContact?: boolean; canAct?: boolean }>()

const LOOK: Record<string, { border: string; badge: string }> = {
  choose_reply: { border: 'border-violet-200 dark:border-violet-900/60', badge: 'badge-violet' },
  handover: { border: 'border-rose-200 dark:border-rose-900/60', badge: 'badge-red' },
  approve_action: { border: 'border-amber-200 dark:border-amber-900/60', badge: 'badge-amber' },
  review_receipt: { border: 'border-emerald-200 dark:border-emerald-900/60', badge: 'badge-green' },
}
// these wait for a verdict on a scenario run, the client's last words are not the point
const RUN_TASKS = ['approve_action', 'review_receipt']
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
const note = ref('')
const p = computed<any>(() => props.task.payload || {})
const isRunTask = computed(() => RUN_TASKS.includes(props.task.kind))

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

type Verdict = 'send' | 'dismiss' | 'release' | 'approve' | 'reject' | 'accept'

const ASK: Partial<Record<Verdict, string>> = {
  dismiss: 'operator.dismiss_confirm',
  reject: 'operator.reject_confirm',
}

async function resolve(action: Verdict) {
  const question = action === 'dismiss' && isRunTask.value ? 'operator.dismiss_action_confirm' : ASK[action]
  if (question && !(await confirm({ message: t(question) }))) return
  const body: Record<string, unknown> = { action, version: seen.value }
  if (action === 'send') {
    body.variant = chosen.value
    body.text = text.value.trim()
  }
  if (props.task.kind === 'review_receipt' && note.value.trim()) body.note = note.value.trim()
  const out = await run(
    () => api<any>(path(`/operator/tasks/${props.task.id}/resolve`), { method: 'POST', body }),
    t(action === 'send' ? 'operator.sent' : 'common.saved'),
  )
  // also on an error (outdated, closed): the parent reloads and shows the new state
  emit('resolved', out ?? null)
}
</script>
