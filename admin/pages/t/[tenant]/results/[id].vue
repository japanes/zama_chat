<template>
  <div class="space-y-5">
    <UiState :loading="loading" :error="error" :has-data="!!data" :retry="load">
      <template v-if="data">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 class="page-title">{{ data.result.scenario_title || data.result.scenario_key }}</h1>
            <p class="page-sub">
              {{ contactName }} · {{ f.dateTime(data.result.started_at) }}
            </p>
          </div>
          <div class="flex items-center gap-2">
            <StatusBadge kind="run" :value="data.result.status" />
            <NuxtLink :to="`${base}/inbox/${data.result.conversation_id}`" class="btn-secondary">
              <UiIcon name="chat" :size="16" /> {{ t('results.open_conversation') }}
            </NuxtLink>
          </div>
        </div>

        <div class="grid gap-5 lg:grid-cols-3">
          <div class="lg:col-span-2 space-y-5">
            <div class="card card-body">
              <h2 class="card-title mb-3">{{ t('results.fields') }}</h2>
              <RunFields :run="data.result" :states="data.result.field_states" />
            </div>

            <div v-if="data.receipts?.length" class="card card-body space-y-3">
              <h2 class="card-title">{{ t('receipts.title') }}</h2>
              <div v-for="r in data.receipts" :key="r.id" class="grid gap-3 md:grid-cols-[9rem_1fr] rounded-button border border-slate-200 dark:border-slate-700 p-3">
                <a :href="r.url" target="_blank" rel="noopener noreferrer" class="block">
                  <img v-if="!r.content_type || r.content_type.startsWith('image/')" :src="r.url" :alt="t('operator.receipt_image')" class="max-h-48 w-full rounded-button object-contain bg-white" loading="lazy" />
                  <span v-else class="text-sm text-primary">{{ t('operator.open_original') }}</span>
                </a>
                <div class="space-y-2 text-sm">
                  <div class="flex flex-wrap items-center gap-2">
                    <span :class="RECEIPT_COLORS[r.status] || 'badge-gray'">{{ t(`receipts.status.${r.status}`) }}</span>
                    <span class="code text-xs">{{ r.field }}</span>
                    <span class="text-xs muted">{{ f.dateTime(r.created_at) }}</span>
                    <span v-if="r.review_reason && r.status === 'review'" class="text-xs muted">{{ t(`operator.review_reason.${r.review_reason}`) }}</span>
                  </div>
                  <ReceiptFacts :extracted="r.extracted" :expected="r.expected" />
                  <ul v-if="r.issues.length" class="space-y-0.5">
                    <li v-for="(x, i) in r.issues" :key="i" class="text-rose-600 text-xs">• {{ t(`receipt_issue.${x.code}`, x.params || {}) }}</li>
                  </ul>
                  <p v-if="r.decided_at" class="text-xs muted">
                    {{ t('receipts.decided', { at: f.dateTime(r.decided_at) }) }}<template v-if="r.decision_note">: {{ r.decision_note }}</template>
                  </p>
                </div>
              </div>
            </div>

            <div class="card">
              <div class="card-body pb-0"><h2 class="card-title">{{ t('results.deliveries') }}</h2></div>
              <UiState :empty="!data.deliveries.length ? t('results.no_deliveries') : false">
                <div class="divide-y divide-slate-100 dark:divide-slate-700">
                  <div v-for="d in data.deliveries" :key="d.id" class="card-body space-y-2">
                    <div class="flex flex-wrap items-center gap-2">
                      <StatusBadge kind="delivery" :value="d.status" />
                      <span class="code">{{ d.event }}</span>
                      <span class="badge-gray">seq {{ d.seq }}</span>
                      <span class="badge-gray code">{{ d.endpoint || '—' }}</span>
                      <span class="text-xs muted">{{ f.dateTime(d.created_at) }}</span>
                      <span v-if="d.attempts" class="text-xs muted">{{ t('results.attempts', { n: d.attempts }) }}</span>
                      <button
                        v-if="can('admin')"
                        type="button"
                        class="btn-sm btn-secondary ml-auto"
                        :disabled="busy"
                        @click="resend(d)"
                      >
                        <UiIcon name="refresh" :size="14" /> {{ t('results.resend') }}
                      </button>
                    </div>
                    <p v-if="d.last_error || d.last_code" class="text-xs text-rose-600 break-words">
                      {{ d.last_code ? `HTTP ${d.last_code}` : '' }} {{ d.last_error }}
                    </p>
                    <JsonView :value="d.payload" collapsible :label="t('results.payload')" />
                  </div>
                </div>
              </UiState>
            </div>
          </div>

          <div class="space-y-5">
            <div class="card card-body space-y-2 text-sm">
              <h2 class="card-title mb-1">{{ t('results.about') }}</h2>
              <Row :label="t('results.scenario')"><span class="code">{{ data.result.scenario_key }} v{{ data.result.scenario_version }}</span></Row>
              <Row :label="t('results.client_language')">{{ data.result.client_language || '—' }}</Row>
              <Row :label="t('results.reply_language')">{{ data.result.reply_language || '—' }}</Row>
              <Row :label="t('results.last_activity')">{{ f.dateTime(data.result.last_activity_at) }}</Row>
              <Row :label="t('results.finished')">{{ f.dateTime(data.result.finished_at) }}</Row>
              <Row :label="t('results.result_id')"><span class="code break-all">{{ data.result.id }}</span></Row>
              <Row v-if="data.result.data?.confirmed_at" :label="t('results.confirmed')">{{ f.dateTime(data.result.data.confirmed_at) }}</Row>
              <Row v-if="data.result.data?.waiting_for" :label="t('results.waiting_for')">
                {{ t(`results.waiting.${data.result.data.waiting_for}`) }}
                <span v-if="data.result.data.waiting_since" class="muted">· {{ f.relative(data.result.data.waiting_since) }}</span>
              </Row>
            </div>
            <div v-if="data.result.data?.external" class="card card-body space-y-2">
              <h2 class="card-title">{{ t('results.external') }}</h2>
              <p class="text-sm"><span class="code">{{ data.result.data.external.event }}</span> · <span class="muted">{{ f.dateTime(data.result.data.external.at) }}</span></p>
              <JsonView v-if="Object.keys(data.result.data.external.data || {}).length" :value="data.result.data.external.data" collapsible :label="t('results.external_data')" />
            </div>
            <div class="card card-body">
              <h2 class="card-title mb-3">{{ t('results.events') }}</h2>
              <ol v-if="data.result.events.length" class="space-y-1.5 text-sm">
                <li v-for="(e, i) in data.result.events" :key="i" class="flex items-baseline gap-2">
                  <span class="text-xs muted whitespace-nowrap">{{ f.dateTime(e.at) }}</span>
                  <span class="code">{{ e.event }}<template v-if="e.field">:{{ e.field }}</template></span>
                </li>
              </ol>
              <p v-else class="text-sm muted">—</p>
            </div>
          </div>
        </div>
      </template>
    </UiState>
  </div>
</template>

<script setup lang="ts">
const { t } = useI18n()
const api = useApi()
const f = useFormat()
const route = useRoute()
const { base, path, can } = useTenant()
const { busy, run } = useAction()

const RECEIPT_COLORS: Record<string, string> = { accepted: 'badge-green', review: 'badge-violet', rejected: 'badge-red' }

const { data, loading, error, load } = useLoader(() =>
  api<any>(path(`/results/${route.params.id}`)),
)
const contactName = computed(() => {
  const c = data.value?.contact
  return c?.name || c?.username || c?.external_id || '—'
})
usePage(() => [
  { label: t('nav.results'), to: `${base.value}/results` },
  { label: data.value?.result?.scenario_key || '…' },
])

async function resend(d: any) {
  const res = await run(
    () => api<any>(path(`/deliveries/${d.id}/resend`), { method: 'POST' }),
    t('results.resent'),
  )
  if (res) load()
}

onMounted(load)
</script>
