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
            <NuxtLink :to="`${base}/conversations/${data.result.conversation_id}`" class="btn-secondary">
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
