<template>
  <div class="rounded-card border border-[#b9aee0] dark:border-violet-500/40 bg-[#eeebf8] dark:bg-violet-500/10 p-3 sm:p-4 space-y-2.5">
    <!-- the bot's reply variants for one client message: tap one, edit if needed, send -->
    <div class="flex flex-wrap items-center gap-2">
      <p class="font-semibold text-slate-900 dark:text-white">{{ t('inbox.variants.title') }}</p>
      <span class="text-sm text-slate-600 dark:text-slate-300">{{ t('inbox.variants.sub') }}</span>
    </div>

    <p v-if="outdated" class="alert-amber flex flex-wrap items-center gap-2">
      <span class="flex-1">{{ t('operator.rebuilt') }}</span>
      <button type="button" class="btn-secondary btn-sm" @click="acceptNew">{{ t('operator.show_new') }}</button>
    </p>

    <div class="space-y-2" role="radiogroup" :aria-label="t('inbox.variants.title')">
      <button
        v-for="(v, i) in variants"
        :key="i"
        type="button"
        role="radio"
        :aria-checked="chosen === i"
        :disabled="!canAct"
        class="w-full flex items-start gap-3 min-h-[44px] rounded-button border bg-white dark:bg-slate-800 px-3 py-2 text-left text-sm leading-snug text-slate-900 dark:text-slate-100 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        :class="chosen === i
          ? 'border-primary ring-2 ring-primary/40'
          : 'border-slate-300 dark:border-slate-600 hover:border-primary'"
        @click="choose(i)"
      >
        <span
          class="mt-0.5 grid place-items-center w-5 h-5 shrink-0 rounded-full border-2"
          :class="chosen === i ? 'border-primary-dark bg-primary-dark text-white' : 'border-slate-300 dark:border-slate-500'"
        >
          <UiIcon v-if="chosen === i" name="check" :size="12" :stroke="3" />
        </span>
        <span class="whitespace-pre-line break-words">{{ v }}</span>
      </button>
    </div>

    <template v-if="canAct">
      <div>
        <label class="label" :for="`reply-${task.id}`">{{ t('inbox.variants.goes') }}</label>
        <textarea
          :id="`reply-${task.id}`"
          v-model="text"
          rows="2"
          class="textarea"
          :placeholder="t('inbox.variants.placeholder')"
          @input="edited = true"
          @keydown.enter.ctrl.prevent="send"
          @keydown.enter.meta.prevent="send"
        />
        <p v-if="edited && chosen !== null" class="hint">{{ t('operator.edited_hint') }}</p>
      </div>
      <div class="flex flex-wrap items-center justify-end gap-2">
        <button type="button" class="btn-ghost" :disabled="busy" @click="resolve('dismiss')">{{ t('inbox.variants.skip') }}</button>
        <button type="button" class="btn-primary" :disabled="busy || !text.trim() || outdated" @click="send">
          <UiIcon name="send" :size="18" /> {{ t('inbox.send') }}
        </button>
      </div>
    </template>
    <p v-else class="alert-amber">{{ t('operator.cannot_write') }}</p>
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{ task: any; canAct?: boolean }>()
const emit = defineEmits<{ resolved: [any] }>()
const { t } = useI18n()
const api = useApi()
const { path } = useTenant()
const { busy, run } = useAction()
const { confirm } = useConfirm()

// the variants the person is looking at: a refresh with new variants does not replace
// them under the cursor — the person presses «show new» first
const seen = ref<number>(props.task.version)
const variants = ref<string[]>([...(props.task.payload?.variants || [])])
const chosen = ref<number | null>(null)
const text = ref('')
const edited = ref(false)
const outdated = computed(() => props.task.version > seen.value)

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

function send() {
  if (busy.value || !text.value.trim() || outdated.value) return
  resolve('send')
}

async function resolve(action: 'send' | 'dismiss') {
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
