<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="page-title">{{ t('nav.sandbox') }}</h1>
        <p class="page-sub">{{ t('sandbox.sub') }}</p>
      </div>
      <button type="button" class="btn-secondary" :disabled="busy" @click="reset">
        <UiIcon name="rollback" :size="16" /> {{ t('sandbox.reset') }}
      </button>
    </div>

    <div class="card card-body grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <div v-if="channels.length > 1">
        <label class="label">{{ t('results.channel') }}</label>
        <select v-model="channelId" class="select" @change="loadState">
          <option v-for="c in channels" :key="c.id" :value="c.id">{{ c.title }}</option>
        </select>
      </div>
      <div>
        <label class="label">{{ t('sandbox.session') }}</label>
        <input v-model.trim="session" class="input" list="sandbox-sessions" pattern="[A-Za-z0-9_-]{1,64}" @change="loadState" />
        <datalist id="sandbox-sessions">
          <option v-for="s in sessions" :key="s.session + s.channel_id" :value="s.session" />
        </datalist>
        <p class="hint">{{ t('sandbox.session_hint') }}</p>
      </div>
      <div>
        <label class="label">{{ t('sandbox.model') }}</label>
        <select v-model="model" class="select">
          <option value="">{{ t('sandbox.model_default') }}</option>
          <option v-for="m in models" :key="m.id" :value="m.name">{{ m.label || m.name }}</option>
        </select>
        <p class="hint">{{ t('sandbox.model_hint') }}</p>
      </div>
      <div v-if="selectedModel?.supports_reasoning">
        <label class="label">{{ t('sandbox.reasoning') }}</label>
        <select v-model="effort" class="select">
          <option :value="null">{{ t('sandbox.model_default') }}</option>
          <option value="">{{ t('models.effort_off') }}</option>
          <option v-for="e in selectedModel.reasoning_efforts || []" :key="e" :value="e">{{ e }}</option>
        </select>
      </div>
      <div>
        <label class="label">{{ t('sandbox.decision_mode') }}</label>
        <select v-model="decisionMode" class="select">
          <option value="">{{ t('sandbox.decision_mode_rules') }}</option>
          <option v-for="m in ['jev', 'llm', 'shadow']" :key="m" :value="m">{{ t(`decisions.mode.${m}`) }}</option>
        </select>
        <p class="hint">{{ t('sandbox.decision_mode_hint') }}</p>
      </div>
    </div>

    <div class="grid gap-5 lg:grid-cols-3">
      <div class="lg:col-span-2 card flex flex-col min-h-[28rem]">
        <div ref="scroller" class="flex-1 card-body overflow-y-auto max-h-[65vh]">
          <UiState :loading="stateLoading" :error="stateError" :has-data="!!state" :retry="loadState">
            <ChatThread :messages="shown" with-trace :preloaded="lastTurn" :open-turn="lastTurn?.turn?.id" />
            <!-- the bot is writing: the answer takes 10–15 seconds -->
            <div v-if="pending" class="mt-3 flex justify-end">
              <div class="rounded-card px-3.5 py-2.5 text-sm bg-primary/70 text-white animate-pulse">{{ t('sandbox.thinking') }}</div>
            </div>
          </UiState>
        </div>
        <div v-if="attachOpen" class="border-t border-slate-200 dark:border-slate-700 px-3 pt-3 flex flex-wrap items-center gap-2">
          <select v-model="attachType" class="select h-9 w-32" :aria-label="t('sandbox.attachment_type')">
            <option v-for="k in ['image', 'file', 'video', 'audio']" :key="k" :value="k">{{ t(`scenarios.attachment_kind.${k}`) }}</option>
          </select>
          <input v-model.trim="attachUrl" type="url" class="input h-9 flex-1 min-w-[12rem]" placeholder="https://…" :aria-label="t('sandbox.attachment_url')" />
          <p class="hint w-full">{{ t('sandbox.attachment_hint') }}</p>
        </div>
        <form class="border-t border-slate-200 dark:border-slate-700 p-3 flex items-end gap-2" @submit.prevent="send">
          <button type="button" class="btn-icon btn-ghost h-10" :class="attachOpen ? 'text-primary' : ''" :aria-label="t('sandbox.attach')" :title="t('sandbox.attach')" @click="attachOpen = !attachOpen">
            <UiIcon name="paperclip" :size="16" />
          </button>
          <textarea
            v-model="text"
            rows="2"
            class="textarea flex-1 resize-none"
            :placeholder="t('sandbox.placeholder')"
            @keydown.enter.exact.prevent="send"
          />
          <button type="submit" class="btn-primary h-10" :disabled="busy || !(text.trim() || attachment)">
            <UiIcon name="send" :size="16" />
            <span class="hidden sm:inline">{{ busy ? t('sandbox.thinking') : t('sandbox.send') }}</span>
          </button>
        </form>
        <p v-if="lastError" class="alert-red m-3 mt-0">{{ lastError }}</p>
        <p v-if="handover" class="alert-amber m-3 mt-0">{{ t('sandbox.handover') }}</p>
      </div>

      <div class="space-y-5">
        <div class="card card-body">
          <h2 class="card-title mb-3">{{ t('chat.runs') }}</h2>
          <div v-if="state?.runs?.length" class="space-y-4">
            <div v-for="r in state.runs" :key="r.id">
              <div class="flex items-center justify-between gap-2 mb-2">
                <span class="code font-medium">{{ r.scenario_key }}</span>
                <StatusBadge kind="run" :value="r.status" />
              </div>
              <RunFields :run="r" />
            </div>
          </div>
          <p v-else class="text-sm muted">{{ t('chat.no_runs') }}</p>
        </div>
        <div class="card card-body text-sm space-y-2">
          <h2 class="card-title mb-1">{{ t('sandbox.about') }}</h2>
          <p class="muted">{{ t('sandbox.about_text') }}</p>
          <NuxtLink v-if="state?.conversation" :to="`${base}/conversations/${state.conversation.id}`" class="btn-ghost btn-sm -ml-2">
            <UiIcon name="external" :size="14" /> {{ t('sandbox.open_conversation') }}
          </NuxtLink>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
const { t } = useI18n()
const api = useApi()
const { errorText, codeText } = useErrorText()
const confirm = useConfirm()
const { base, path } = useTenant()
const { channels, load: loadInfo } = useTenantInfo()
usePage(() => [{ label: t('nav.sandbox') }])

const channelId = ref('')
const session = ref('default')
const sessions = ref<any[]>([])
const models = ref<any[]>([])
const model = ref('')
const effort = ref<string | null>(null)
const decisionMode = ref('')
const text = ref('')
// a file by its address: receipts and photos without a real channel
const attachOpen = ref(false)
const attachType = ref('image')
const attachUrl = ref('')
const attachment = computed(() => (attachOpen.value && /^https?:\/\//.test(attachUrl.value) ? { type: attachType.value, url: attachUrl.value } : null))
const busy = ref(false)
const state = ref<any>(null)
// the client's message is shown at once; the server's list replaces it with the answer
const pending = ref<any>(null)
const shown = computed(() => [...(state.value?.messages || []), ...(pending.value ? [pending.value] : [])])
const stateLoading = ref(false)
const stateError = ref<string | null>(null)
const lastTurn = ref<any>(null)
const lastError = ref<string | null>(null)
const handover = ref(false)
const scroller = ref<HTMLElement | null>(null)

const selectedModel = computed(() => models.value.find((m) => m.name === model.value) || null)
watch(model, () => (effort.value = null))

const SESSION_RE = /^[A-Za-z0-9_-]{1,64}$/

function scope() {
  return { channel_id: channelId.value || undefined, session: session.value }
}

async function loadState() {
  if (!SESSION_RE.test(session.value)) return
  stateLoading.value = true
  stateError.value = null
  lastTurn.value = null
  try {
    state.value = (await api<{ state: any }>(path('/sandbox'), { query: scope() })).state
  } catch (e) {
    stateError.value = errorText(e)
  } finally {
    stateLoading.value = false
    scrollDown()
  }
}

async function send() {
  const body = text.value.trim()
  const files = attachment.value ? [attachment.value] : []
  if ((!body && !files.length) || busy.value) return
  busy.value = true
  lastError.value = null
  pending.value = { id: 'pending', role: 'client', text: body, attachments: files, created_at: new Date().toISOString() }
  text.value = ''
  scrollDown()
  try {
    const res = await api<any>(path('/sandbox/turn'), {
      method: 'POST',
      body: {
        ...scope(),
        text: body || null,
        attachments: files,
        model: model.value || null,
        reasoning_effort: selectedModel.value?.supports_reasoning ? effort.value : null,
        decision_mode: decisionMode.value || null,
      },
    })
    attachUrl.value = ''
    attachOpen.value = false
    state.value = res.state
    lastTurn.value = res.turn
    handover.value = !!res.handover
    if (res.error) lastError.value = codeText(res.error)
    loadSessions()
  } catch (e) {
    lastError.value = errorText(e)
    // not sent: the text goes back to the input (unless a new one is typed already)
    if (!text.value) text.value = body
  } finally {
    pending.value = null
    busy.value = false
    scrollDown()
  }
}

async function reset() {
  const ok = await confirm.confirm({ message: t('sandbox.reset_confirm'), tone: 'danger', confirmLabel: t('sandbox.reset') })
  if (!ok) return
  busy.value = true
  try {
    await api(path('/sandbox'), { method: 'DELETE', query: scope() })
    state.value = null
    lastTurn.value = null
    lastError.value = null
    handover.value = false
    loadSessions()
  } catch (e) {
    lastError.value = errorText(e)
  } finally {
    busy.value = false
  }
}

async function loadSessions() {
  try {
    sessions.value = (await api<{ items: any[] }>(path('/sandbox/sessions'))).items
  } catch {
    sessions.value = []
  }
}

function scrollDown() {
  nextTick(() => {
    if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
  })
}

onMounted(async () => {
  try {
    await loadInfo()
  } catch {
    // the channel select stays hidden; the server takes the first channel
  }
  channelId.value = channels.value[0]?.id || ''
  loadState()
  loadSessions()
  try {
    const all = (await api<{ items: any[] }>('/models')).items
    models.value = all.filter((m) => m.kind === 'language' && m.is_active)
  } catch {
    models.value = []
  }
})
</script>
