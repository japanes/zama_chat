<template>
  <div class="space-y-5">
    <UiState :loading="loading" :error="error" :has-data="!!data" :retry="load">
      <template v-if="data">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 class="page-title">{{ contactName }}</h1>
            <p class="page-sub">
              {{ data.channel?.title }}
              <template v-if="data.conversation.client_language"> · {{ t('chat.languages', { client: data.conversation.client_language, reply: data.conversation.reply_language || '—' }) }}</template>
            </p>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <span v-if="data.conversation.sandbox" class="badge-amber">{{ t('nav.sandbox') }}</span>
            <span v-if="data.conversation.handover" class="badge-red">{{ t('chat.handover') }}</span>
            <span v-if="data.conversation.operator_mode" class="badge-violet">{{ t('chat.operator_mode') }}</span>
            <template v-if="!data.conversation.sandbox">
              <button v-if="data.conversation.handover" type="button" class="btn-secondary btn-sm" :disabled="busy" @click="act('release')">
                {{ t('operator.release') }}
              </button>
              <button v-else type="button" class="btn-secondary btn-sm" :disabled="busy" @click="act('handover')">
                {{ t('operator.take_over') }}
              </button>
              <label class="flex items-center gap-2 text-sm">
                <input type="checkbox" class="checkbox" :checked="data.conversation.operator_mode" :disabled="busy" @change="setOperatorMode(($event.target as HTMLInputElement).checked)" />
                {{ t('chat.operator_mode') }}
              </label>
            </template>
            <button type="button" class="btn-secondary btn-sm" :disabled="loading" @click="load">
              <UiIcon name="refresh" :size="14" /> {{ t('common.refresh') }}
            </button>
          </div>
        </div>

        <div class="grid gap-5 lg:grid-cols-3">
          <div class="lg:col-span-2 space-y-5">
            <TaskCard
              v-for="task in data.tasks || []"
              :key="task.id"
              :task="task"
              can-act
              @resolved="load"
            />

            <div class="card card-body">
              <ChatThread :messages="data.messages" with-trace />
            </div>

            <!-- the operator writes to the client directly -->
            <form v-if="!data.conversation.sandbox" class="card card-body space-y-2" @submit.prevent="send">
              <label class="label">{{ t('operator.write') }}</label>
              <textarea v-model="reply" rows="3" class="textarea" :disabled="!data.operator_can_write" :placeholder="t('operator.reply_placeholder')" />
              <div class="flex flex-wrap items-center justify-between gap-2">
                <p class="hint">
                  <template v-if="!data.operator_can_write">{{ t('operator.cannot_write') }}</template>
                  <template v-else-if="!data.conversation.handover">{{ t('operator.write_hint') }}</template>
                </p>
                <button type="submit" class="btn-primary btn-sm" :disabled="busy || !reply.trim() || !data.operator_can_write">
                  <UiIcon name="send" :size="14" /> {{ t('operator.send') }}
                </button>
              </div>
            </form>
          </div>

          <div class="space-y-5">
            <div class="card card-body">
              <h2 class="card-title mb-3">{{ t('chat.runs') }}</h2>
              <div v-if="data.runs.length" class="space-y-3">
                <NuxtLink
                  v-for="r in data.runs"
                  :key="r.id"
                  :to="`${base}/results/${r.id}`"
                  class="block rounded-button border border-slate-200 dark:border-slate-700 p-3 hover:border-primary"
                >
                  <div class="flex items-center justify-between gap-2">
                    <span class="code font-medium">{{ r.scenario_key }}</span>
                    <StatusBadge kind="run" :value="r.status" />
                  </div>
                  <p class="mt-1 text-xs muted">
                    {{ t('chat.fields_progress', { filled: Object.keys(r.fields || {}).length, total: Object.keys(r.fields || {}).length + (r.missing?.length || 0) }) }}
                    <template v-if="r.asking"> · {{ t('chat.asking', { field: r.asking }) }}</template>
                  </p>
                </NuxtLink>
              </div>
              <p v-else class="text-sm muted">{{ t('chat.no_runs') }}</p>
            </div>

            <div class="card card-body">
              <h2 class="card-title mb-3">{{ t('chat.turns') }}</h2>
              <ul v-if="data.turns.length" class="space-y-1.5 text-sm">
                <li v-for="tr in data.turns" :key="tr.id">
                  <button type="button" class="w-full flex items-center gap-2 text-left hover:text-primary" @click="openTurn = tr.id">
                    <StatusBadge kind="turn" :value="tr.status" />
                    <span class="flex-1 truncate">{{ tr.input_text }}</span>
                    <span class="text-xs muted whitespace-nowrap">{{ f.money(tr.cost) }}</span>
                  </button>
                </li>
              </ul>
              <p v-else class="text-sm muted">—</p>
            </div>

            <div class="card card-body space-y-2 text-sm">
              <h2 class="card-title mb-1">{{ t('chat.client') }}</h2>
              <Row :label="t('chat.external_id')"><span class="code break-all">{{ data.contact?.external_id }}</span></Row>
              <Row v-if="data.contact?.username" :label="t('chat.username')">{{ data.contact.username }}</Row>
              <Row :label="t('chat.last_client')">{{ f.dateTime(data.conversation.last_client_at) }}</Row>
              <Row :label="t('chat.last_bot')">{{ f.dateTime(data.conversation.last_bot_at) }}</Row>
              <Row v-if="data.conversation.window_closes_at" :label="t('chat.window')">
                <span :class="windowOpen ? '' : 'text-rose-600'">
                  {{ windowOpen ? t('chat.window_until', { at: f.dateTime(data.conversation.window_closes_at) }) : t('chat.window_closed') }}
                </span>
              </Row>
              <Row :label="t('chat.created')">{{ f.dateTime(data.conversation.created_at) }}</Row>
            </div>
          </div>
        </div>
      </template>
    </UiState>

    <!-- a turn from the list: also the ones without a bot message (failed, dropped) -->
    <UiModal :open="!!openTurn" :title="t('chat.turn')" size="xl" @close="openTurn = null">
      <TurnPanel v-if="openTurn" :key="openTurn" :turn-id="openTurn" />
    </UiModal>
  </div>
</template>

<script setup lang="ts">
const { t } = useI18n()
const api = useApi()
const f = useFormat()
const route = useRoute()
const { base, path } = useTenant()
const { busy, run } = useAction()
const openTurn = ref<string | null>(null)
const reply = ref('')
const POLL_MS = 5000

const convPath = computed(() => path(`/conversations/${route.params.id}`))
const { data, loading, error, load } = useLoader(() => api<any>(convPath.value))
const contactName = computed(() => {
  const c = data.value?.contact
  return c?.name || c?.username || c?.external_id || '—'
})
const windowOpen = computed(() => {
  const at = data.value?.conversation?.window_closes_at
  return !at || new Date(at).getTime() > Date.now()
})
usePage(() => [
  { label: t('nav.conversations'), to: `${base.value}/conversations` },
  { label: contactName.value },
])

async function send() {
  const res = await run(
    () => api(`${convPath.value}/messages`, { method: 'POST', body: { text: reply.value.trim() } }),
    t('operator.sent'),
  )
  if (res) {
    reply.value = ''
    load()
  }
}

async function act(action: 'handover' | 'release') {
  const res = await run(() => api(`${convPath.value}/${action}`, { method: 'POST' }), t('common.saved'))
  if (res) load()
}

async function setOperatorMode(on: boolean) {
  const res = await run(() => api(convPath.value, { method: 'PATCH', body: { operator_mode: on } }), t('common.saved'))
  load()
  return res
}

// a live conversation: refresh quietly while the tab is visible
let timer: ReturnType<typeof setInterval> | null = null
onMounted(() => {
  load()
  timer = setInterval(() => {
    if (!document.hidden && !loading.value && !busy.value) load()
  }, POLL_MS)
})
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})
</script>
