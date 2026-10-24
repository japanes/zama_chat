<template>
  <div class="h-full overflow-y-auto xl:overflow-hidden">
    <!-- The sandbox: a chat with the bot on behalf of a client and, next to it, why the bot
         answered this way and what it has learned. On a wide screen the page fills the area
         under the header (page meta `fill`) and its panes scroll on their own. -->
    <div class="max-w-[1180px] mx-auto flex flex-col gap-4 p-4 md:p-6 lg:px-8 xl:h-full">
      <header class="flex flex-wrap items-end justify-between gap-3">
        <div class="min-w-0">
          <h1 class="page-title">{{ t('nav.sandbox') }}</h1>
          <p class="page-sub">{{ t('sandbox.sub') }}</p>
        </div>
        <button type="button" class="btn-secondary" :disabled="busy || !state" @click="reset">
          <UiIcon name="rollback" :size="16" /> {{ t('sandbox.reset') }}
        </button>
      </header>

      <div class="flex flex-wrap items-center gap-x-5 gap-y-2.5">
        <div class="flex items-center gap-2">
          <label :for="ids.who" class="text-sm font-semibold text-slate-600 dark:text-slate-300">{{ t('sandbox.who') }}</label>
          <select :id="ids.who" class="select w-auto" :value="session" :disabled="busy" @change="pickClient(($event.target as HTMLSelectElement).value)">
            <option v-for="name in clients" :key="name" :value="name">{{ clientLabel(name) }}</option>
            <option :value="ADD">{{ t('sandbox.client_add') }}</option>
          </select>
        </div>
        <div v-if="channels.length > 1" class="flex items-center gap-2">
          <label :for="ids.where" class="text-sm font-semibold text-slate-600 dark:text-slate-300">{{ t('sandbox.where') }}</label>
          <select :id="ids.where" v-model="channelId" class="select w-auto" :disabled="busy" @change="loadState">
            <option v-for="c in channels" :key="c.id" :value="c.id">{{ c.title }}</option>
          </select>
        </div>
        <button type="button" class="btn-ghost ml-auto text-primary-dark dark:text-sky-300" :aria-expanded="settingsOpen" @click="settingsOpen = !settingsOpen">
          <UiIcon name="settings" :size="16" /> {{ t('sandbox.settings') }}
          <span v-if="settingsChanged" class="badge-amber">{{ t('sandbox.settings_changed') }}</span>
        </button>
      </div>

      <div v-if="settingsOpen" class="card card-body space-y-3">
        <p class="text-sm muted">{{ t('sandbox.settings_sub') }}</p>
        <div class="grid gap-x-4 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
          <div>
            <label class="label" :for="ids.model">{{ t('sandbox.model') }}</label>
            <select :id="ids.model" v-model="model" class="select">
              <option value="">{{ t('sandbox.as_clients') }}</option>
              <option v-for="m in models" :key="m.id" :value="m.name">{{ m.label || m.name }}</option>
            </select>
          </div>
          <div v-if="selectedModel?.supports_reasoning">
            <label class="label" :for="ids.effort">{{ t('sandbox.reasoning') }}</label>
            <select :id="ids.effort" v-model="effort" class="select">
              <option :value="null">{{ t('sandbox.as_clients') }}</option>
              <option value="">{{ t('sandbox.reasoning_off') }}</option>
              <option v-for="e in selectedModel.reasoning_efforts || []" :key="e" :value="e">{{ e }}</option>
            </select>
          </div>
          <div>
            <label class="label" :for="ids.decide">{{ t('sandbox.decision_mode') }}</label>
            <select :id="ids.decide" v-model="decisionMode" class="select">
              <option value="">{{ t('sandbox.as_clients') }}</option>
              <option v-for="m in ['jev', 'llm', 'shadow']" :key="m" :value="m">{{ t(`sandbox.decide.${m}`) }}</option>
            </select>
          </div>
        </div>
      </div>

      <div class="grid gap-5 xl:flex-1 xl:min-h-0 xl:grid-cols-[minmax(0,1fr)_380px]">
        <section :aria-label="t('sandbox.chat')" class="card flex flex-col overflow-hidden h-[75dvh] min-h-[26rem] xl:h-auto xl:min-h-0">
          <div ref="scroller" class="flex-1 min-h-0 overflow-y-auto p-4 sm:p-5 bg-slate-50 dark:bg-slate-900/40">
            <UiState :loading="stateLoading && !state" :error="stateError" :has-data="!stateLoading || !!state" :retry="loadState">
              <template v-if="shown.length">
                <ChatThread
                  :messages="shown"
                  explainable
                  :selected-turn="whyId"
                  :selected-note="t(wide ? 'sandbox.why_here' : 'sandbox.why_below')"
                  @explain="explain"
                />
                <!-- the answer takes 10–15 seconds: say that the bot is at work -->
                <div v-if="busy && pending" class="mt-4 flex justify-end" role="status">
                  <div class="rounded-[14px_14px_4px_14px] px-4 py-2.5 text-[15px] bg-primary-dark/70 text-white animate-pulse">{{ t('sandbox.thinking') }}</div>
                </div>
              </template>
              <div v-else class="h-full min-h-[14rem] grid place-items-center text-center">
                <div class="max-w-sm">
                  <p class="font-semibold text-slate-900 dark:text-white">{{ t('sandbox.start') }}</p>
                  <p class="mt-1 text-sm muted">{{ t('sandbox.start_sub') }}</p>
                </div>
              </div>
            </UiState>
          </div>

          <div class="border-t border-slate-200 dark:border-slate-700 p-3 sm:p-4 space-y-2.5">
            <p v-if="lastError" class="alert-red" role="alert">{{ lastError }}</p>
            <p v-if="handover" class="alert-amber">{{ t('sandbox.handover') }}</p>

            <div class="flex flex-wrap items-center gap-2">
              <span class="text-[13px] muted">{{ t('sandbox.try') }}</span>
              <button v-for="p in phrases" :key="p" type="button" class="chip" :disabled="busy" @click="send(p)">{{ p }}</button>
            </div>

            <div v-if="attachOpen" class="flex flex-wrap items-center gap-2">
              <select v-model="attachType" class="select w-36" :aria-label="t('sandbox.attachment_type')">
                <option v-for="k in ['image', 'file', 'video', 'audio']" :key="k" :value="k">{{ t(`scenarios.attachment_kind.${k}`) }}</option>
              </select>
              <input v-model.trim="attachUrl" type="url" class="input flex-1 min-w-[12rem]" placeholder="https://…" :aria-label="t('sandbox.attachment_url')" />
              <p class="hint w-full">{{ t('sandbox.attachment_hint') }}</p>
            </div>

            <form class="flex items-end gap-2" @submit.prevent="send()">
              <button
                type="button"
                class="btn-secondary btn-icon shrink-0"
                :class="attachOpen ? 'text-primary-dark dark:text-sky-300' : ''"
                :aria-label="t('sandbox.attach')"
                :aria-expanded="attachOpen"
                :title="t('sandbox.attach')"
                @click="attachOpen = !attachOpen"
              >
                <UiIcon name="paperclip" :size="18" />
              </button>
              <textarea
                ref="input"
                v-model="text"
                rows="2"
                class="textarea flex-1 min-w-0 resize-none"
                :aria-label="t('sandbox.message_label')"
                :placeholder="t('sandbox.placeholder')"
                @keydown.enter.exact.prevent="send()"
              />
              <button type="submit" class="btn-primary shrink-0" :disabled="busy || !(text.trim() || attachment)">
                <UiIcon name="send" :size="16" />
                <span class="hidden sm:inline">{{ t('sandbox.send') }}</span>
              </button>
            </form>
            <p class="text-[13px] muted">{{ t('sandbox.slow') }}</p>
          </div>
        </section>

        <aside class="space-y-5 xl:min-h-0 xl:overflow-y-auto xl:pr-1">
          <section class="card card-body" aria-live="polite">
            <h2 class="card-title mb-3">{{ t('why.title') }}</h2>
            <TurnWhy v-if="why" :detail="why" :runs="state?.runs || []" />
            <p v-else-if="whyError" class="alert-red">{{ whyError }}</p>
            <p v-else class="text-sm muted">{{ whyId ? t('why.loading') : t('why.empty') }}</p>
          </section>

          <section class="card card-body">
            <h2 class="card-title mb-3">{{ t('sandbox.learned') }}</h2>
            <template v-if="state?.runs?.length">
              <div v-for="(r, i) in state.runs" :key="r.id" :class="i ? 'mt-4 pt-4 border-t border-slate-200 dark:border-slate-700' : ''">
                <RunProgress :run="r" />
                <NuxtLink :to="`${base}/scenarios/${r.scenario_key}`" class="link inline-block mt-3 text-sm">{{ t('sandbox.open_scenario') }}</NuxtLink>
              </div>
            </template>
            <p v-else class="text-sm muted">{{ t('sandbox.learned_empty') }}</p>
          </section>

          <NuxtLink v-if="state?.conversation" :to="`${base}/inbox/${state.conversation.id}`" class="btn-ghost btn-sm">
            <UiIcon name="external" :size="14" /> {{ t('sandbox.open_conversation') }}
          </NuxtLink>
        </aside>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
definePageMeta({ fill: true })

const { t } = useI18n()
const api = useApi()
const { errorText, codeText } = useErrorText()
const confirm = useConfirm()
const { base, path } = useTenant()
const { channels, load: loadInfo } = useTenantInfo()
usePage(() => [{ label: t('nav.sandbox') }])

const ids = { who: useId(), where: useId(), model: useId(), effort: useId(), decide: useId() }
// the explanation stands to the right of the chat on a wide screen, under it otherwise
const wide = useMediaQuery('(min-width: 1280px)')

// --- who writes and where ---
// a test client is a session name for the server; a person sees «Test client 2»
const FIRST = 'default'
const ADD = '+'
const channelId = ref('')
const session = ref(FIRST)
const sessions = ref<any[]>([])

const clients = computed(() => {
  const here = sessions.value.filter((s) => !channelId.value || s.channel_id === channelId.value).map((s) => s.session as string)
  return [...new Set([FIRST, ...here, session.value])].sort((a, b) => clientNumber(a) - clientNumber(b) || a.localeCompare(b))
})
function clientNumber(name: string): number {
  if (name === FIRST) return 1
  const m = /^client-(\d+)$/.exec(name)
  return m ? Number(m[1]) : Number.MAX_SAFE_INTEGER
}
function clientLabel(name: string): string {
  if (name === FIRST) return t('sandbox.client_first')
  const n = clientNumber(name)
  return n === Number.MAX_SAFE_INTEGER ? name : t('sandbox.client_n', { n })
}
function pickClient(value: string) {
  if (value === ADD) {
    const taken = clients.value.map(clientNumber).filter((n) => n !== Number.MAX_SAFE_INTEGER)
    session.value = `client-${Math.max(1, ...taken) + 1}`
  } else {
    session.value = value
  }
  loadState()
}
function scope() {
  return { channel_id: channelId.value || undefined, session: session.value }
}

// --- test settings: empty = the same as for real clients ---
const settingsOpen = ref(false)
const models = ref<any[]>([])
const model = ref('')
const effort = ref<string | null>(null)
const decisionMode = ref('')
const selectedModel = computed(() => models.value.find((m) => m.name === model.value) || null)
const settingsChanged = computed(() => !!model.value || !!decisionMode.value)
watch(model, () => (effort.value = null))

// --- the conversation ---
const text = ref('')
const input = ref<HTMLTextAreaElement | null>(null)
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
const lastError = ref<string | null>(null)
const handover = ref(false)
const scroller = ref<HTMLElement | null>(null)
const phrases = computed(() => ['price', 'cancel', 'later', 'human'].map((k) => t(`sandbox.phrase.${k}`)))

// --- why the bot answered this way: the turn picked in the chat ---
const whyId = ref<string | null>(null)
const whyError = ref<string | null>(null)
const details = reactive(new Map<string, any>())
const why = computed(() => (whyId.value ? details.get(whyId.value) || null : null))

async function explain(turnId: string | null) {
  whyId.value = turnId
  whyError.value = null
  if (!turnId || details.has(turnId)) return
  try {
    details.set(turnId, await api<any>(path(`/turns/${turnId}`)))
  } catch (e) {
    if (whyId.value === turnId) whyError.value = errorText(e)
  }
}

async function loadState() {
  stateLoading.value = true
  stateError.value = null
  lastError.value = null
  handover.value = false
  state.value = null
  whyId.value = null
  const asked = JSON.stringify(scope())
  try {
    const res = await api<{ state: any }>(path('/sandbox'), { query: scope() })
    if (asked !== JSON.stringify(scope())) return // another client was picked meanwhile
    state.value = res.state
    // back in a conversation: the last answer is explained at once (turns come newest first;
    // the answers written in the background are no answer)
    explain(state.value?.turns?.find((x: any) => x.kind !== 'scenario_answers')?.id || null)
  } catch (e) {
    stateError.value = errorText(e)
  } finally {
    stateLoading.value = false
    scrollDown()
  }
}

// `phrase` — a ready message from the chips under the chat
async function send(phrase?: string) {
  const body = (phrase ?? text.value).trim()
  const files = !phrase && attachment.value ? [attachment.value] : []
  if ((!body && !files.length) || busy.value) return
  busy.value = true
  lastError.value = null
  pending.value = { id: 'pending', role: 'client', text: body, attachments: files, created_at: new Date().toISOString() }
  if (!phrase) text.value = ''
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
    if (files.length) {
      attachUrl.value = ''
      attachOpen.value = false
    }
    state.value = res.state
    handover.value = !!res.handover
    if (res.error) lastError.value = codeText(res.error)
    // the new answer is explained at once
    if (res.turn?.turn?.id) {
      details.set(res.turn.turn.id, res.turn)
      whyId.value = res.turn.turn.id
      whyError.value = null
    }
    loadSessions()
  } catch (e) {
    lastError.value = errorText(e)
    // not sent: the text goes back to the input (unless a new one is typed already)
    if (!phrase && !text.value) text.value = body
  } finally {
    pending.value = null
    busy.value = false
    scrollDown()
    if (wide.value) input.value?.focus()
  }
}

async function reset() {
  const ok = await confirm.confirm({ message: t('sandbox.reset_confirm'), tone: 'danger', confirmLabel: t('sandbox.reset') })
  if (!ok) return
  busy.value = true
  try {
    await api(path('/sandbox'), { method: 'DELETE', query: scope() })
    state.value = null
    whyId.value = null
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
