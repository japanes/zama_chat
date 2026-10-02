<template>
  <div class="space-y-5">
    <div>
      <h1 class="page-title">{{ t('nav.results') }}</h1>
      <p class="page-sub">{{ t('results.sub') }}</p>
    </div>

    <div class="card card-body flex flex-wrap items-end gap-3">
      <div class="w-full sm:w-56">
        <label class="label">{{ t('common.search') }}</label>
        <input v-model.trim="filters.q" class="input" :placeholder="t('results.search_placeholder')" @keyup.enter="reload" />
      </div>
      <div class="w-full sm:w-44">
        <label class="label">{{ t('results.scenario') }}</label>
        <select v-model="filters.scenario" class="select" @change="reload">
          <option value="">{{ t('common.all') }}</option>
          <option v-for="s in scenarios" :key="s.key" :value="s.key">{{ s.title || s.key }}</option>
        </select>
      </div>
      <div v-if="channels.length > 1" class="w-full sm:w-44">
        <label class="label">{{ t('results.channel') }}</label>
        <select v-model="filters.channel_id" class="select" @change="reload">
          <option value="">{{ t('common.all') }}</option>
          <option v-for="c in channels" :key="c.id" :value="c.id">{{ c.title }}</option>
        </select>
      </div>
      <div class="w-full sm:w-40">
        <label class="label">{{ t('results.since') }}</label>
        <input v-model="filters.since" type="date" class="input" @change="reload" />
      </div>
      <div class="w-full sm:w-40">
        <label class="label">{{ t('results.until') }}</label>
        <input v-model="filters.until" type="date" class="input" @change="reload" />
      </div>
      <div class="w-full">
        <label class="label">{{ t('results.status') }}</label>
        <div class="flex flex-wrap gap-1.5">
          <button
            v-for="s in STATUSES"
            :key="s"
            type="button"
            class="btn-sm rounded-badge border"
            :class="filters.status.includes(s)
              ? 'border-primary bg-primary/10 text-primary'
              : 'border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300'"
            @click="toggleStatus(s)"
          >
            {{ t(`status.run.${s}`) }}
          </button>
        </div>
      </div>
    </div>

    <div class="card overflow-hidden">
      <UiState :loading="loading" :error="error" :empty="!loading && !items.length" :has-data="items.length > 0" :retry="load">
        <div class="overflow-x-auto">
          <table class="table table-hover">
            <thead>
              <tr>
                <th>{{ t('results.started') }}</th>
                <th>{{ t('results.client') }}</th>
                <th>{{ t('results.scenario') }}</th>
                <th>{{ t('results.status') }}</th>
                <th>{{ t('results.progress') }}</th>
                <th>{{ t('results.delivery') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in items" :key="r.id" @click="navigateTo(`${base}/results/${r.id}`)">
                <td class="whitespace-nowrap">{{ f.dateTime(r.started_at) }}</td>
                <td>
                  <span class="font-medium">{{ r.contact?.name || r.contact?.username || r.contact?.external_id }}</span>
                  <p v-if="r.contact?.username && r.contact?.name" class="text-xs muted">{{ r.contact.username }}</p>
                </td>
                <td><span class="code">{{ r.scenario_key }}</span> <span class="text-xs muted">v{{ r.scenario_version }}</span></td>
                <td><StatusBadge kind="run" :value="r.status" /></td>
                <td class="whitespace-nowrap">
                  <span class="text-sm">{{ filled(r) }} / {{ filled(r) + (r.missing?.length || 0) }}</span>
                </td>
                <td>
                  <template v-if="r.last_delivery">
                    <StatusBadge kind="delivery" :value="r.last_delivery.status" />
                    <p class="text-xs muted mt-0.5">{{ r.last_delivery.event }}</p>
                  </template>
                  <span v-else class="muted">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <UiPagination v-model:offset="offset" :total="total" :limit="LIMIT" />
      </UiState>
    </div>
  </div>
</template>

<script setup lang="ts">
const { t } = useI18n()
const api = useApi()
const f = useFormat()
const { base, path } = useTenant()
const { channels, load: loadInfo } = useTenantInfo()
usePage(() => [{ label: t('nav.results') }])

const STATUSES = ['focus', 'paused', 'waiting', 'completed', 'cancelled', 'abandoned']
const LIMIT = 50

const filters = reactive({ q: '', scenario: '', channel_id: '', since: '', until: '', status: [] as string[] })
const offset = ref(0)
const total = ref(0)
const items = ref<any[]>([])
const scenarios = ref<any[]>([])

const { loading, error, load } = useLoader(async () => {
  const query: Record<string, any> = { limit: LIMIT, offset: offset.value }
  if (filters.q) query.q = filters.q
  if (filters.scenario) query.scenario = filters.scenario
  if (filters.channel_id) query.channel_id = filters.channel_id
  if (filters.status.length) query.status = filters.status.join(',')
  // dates are days in the user's time zone
  if (filters.since) query.since = new Date(`${filters.since}T00:00:00`).toISOString()
  if (filters.until) query.until = new Date(`${filters.until}T23:59:59`).toISOString()
  const res = await api<{ items: any[]; total: number }>(path('/results'), { query })
  items.value = res.items
  total.value = res.total
  return res
})

function reload() {
  offset.value = 0
  load()
}
function toggleStatus(s: string) {
  filters.status = filters.status.includes(s) ? filters.status.filter((x) => x !== s) : [...filters.status, s]
  reload()
}
function filled(r: any) {
  return Object.keys(r.fields || {}).length
}

watch(offset, () => load())
onMounted(async () => {
  load()
  loadInfo().catch(() => {})
  try {
    scenarios.value = (await api<{ items: any[] }>(path('/scenarios'))).items
  } catch {
    scenarios.value = []
  }
})
</script>
