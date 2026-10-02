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
    </div>

    <div class="grid gap-5 lg:grid-cols-3">
      <div class="lg:col-span-2 card flex flex-col min-h-[28rem]">
        <div ref="scroller" class="flex-1 card-body overflow-y-auto max-h-[65vh]">
          <UiState :loading="stateLoading" :error="stateError" :has-data="!!state" :retry="loadState">
            <ChatThread :messages="state?.messages || []" with-trace :preloaded="lastTurn" :open-turn="lastTurn?.turn?.id" />
          </UiState>
        </div>
        <form class="border-t border-slate-200 dark:border-slate-700 p-3 flex items-end gap-2" @submit.prevent="send">
          <textarea
            v-model="text"
            rows="2"
            class="textarea flex-1 resize-none"
            :placeholder="t('sandbox.placeholder')"
            @keydown.enter.exact.prevent="send"
          />
          <button type="submit" class="btn-primary h-10" :disabled="busy || !text.trim()">
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
const text = ref('')
const busy = ref(false)
const state = ref<any>(null)
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
  if (!body || busy.value) return
  busy.value = true
  lastError.value = null
  try {
    const res = await api<any>(path('/sandbox/turn'), {
      method: 'POST',
      body: {
        ...scope(),
        text: body,
        model: model.value || null,
        reasoning_effort: selectedModel.value?.supports_reasoning ? effort.value : null,
      },
    })
    text.value = ''
    state.value = res.state
    lastTurn.value = res.turn
    handover.value = !!res.handover
    if (res.error) lastError.value = codeText(res.error)
    loadSessions()
  } catch (e) {
    lastError.value = errorText(e)
  } finally {
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
