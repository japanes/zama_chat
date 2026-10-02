<template>
  <div class="space-y-5">
    <div>
      <h1 class="page-title">{{ t('nav.conversations') }}</h1>
      <p class="page-sub">{{ t('chat.sub') }}</p>
    </div>

    <div class="card card-body flex flex-wrap items-end gap-3">
      <div class="w-full sm:w-64">
        <label class="label">{{ t('common.search') }}</label>
        <input v-model.trim="filters.q" class="input" :placeholder="t('chat.search_placeholder')" @keyup.enter="reload" />
      </div>
      <div v-if="channels.length > 1" class="w-full sm:w-48">
        <label class="label">{{ t('results.channel') }}</label>
        <select v-model="filters.channel_id" class="select" @change="reload">
          <option value="">{{ t('common.all') }}</option>
          <option v-for="c in channels" :key="c.id" :value="c.id">{{ c.title }}</option>
        </select>
      </div>
      <label class="flex items-center gap-2 text-sm h-9">
        <input v-model="filters.handover" type="checkbox" class="checkbox" @change="reload" />
        {{ t('chat.only_handover') }}
      </label>
      <label class="flex items-center gap-2 text-sm h-9">
        <input v-model="filters.sandbox" type="checkbox" class="checkbox" @change="reload" />
        {{ t('chat.sandbox_only') }}
      </label>
    </div>

    <div class="card overflow-hidden">
      <UiState :loading="loading" :error="error" :empty="!loading && !items.length" :has-data="items.length > 0" :retry="load">
        <ul class="divide-y divide-slate-100 dark:divide-slate-700">
          <li v-for="c in items" :key="c.id">
            <NuxtLink
              :to="`${base}/conversations/${c.id}`"
              class="flex items-start gap-3 px-4 py-3 hover:bg-slate-50 dark:hover:bg-slate-700/40"
            >
              <span class="grid place-items-center w-9 h-9 shrink-0 rounded-full bg-slate-100 dark:bg-slate-700 text-sm font-semibold text-slate-600 dark:text-slate-200">
                {{ initials(c.contact) }}
              </span>
              <div class="flex-1 min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <span class="font-medium truncate">{{ contactName(c.contact) }}</span>
                  <span v-if="c.handover" class="badge-red">{{ t('chat.handover') }}</span>
                  <span v-if="c.operator_mode" class="badge-violet">{{ t('chat.operator_mode') }}</span>
                  <span v-for="r in c.open_runs || []" :key="r.id" class="badge-blue code">{{ r.scenario_key }}</span>
                  <span class="ml-auto text-xs muted whitespace-nowrap">{{ f.relative(c.updated_at) }}</span>
                </div>
                <p v-if="c.last_message" class="mt-0.5 text-sm muted truncate">
                  <span v-if="c.last_message.role !== 'client'" class="font-medium">{{ t(`chat.role.${c.last_message.role}`) }}: </span>{{ c.last_message.text }}
                </p>
                <p class="mt-0.5 text-xs muted">
                  {{ c.channel_title }}
                  <template v-if="c.client_language"> · {{ c.client_language }} → {{ c.reply_language || '—' }}</template>
                </p>
              </div>
            </NuxtLink>
          </li>
        </ul>
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
usePage(() => [{ label: t('nav.conversations') }])

const LIMIT = 50
const filters = reactive({ q: '', channel_id: '', handover: false, sandbox: false })
const offset = ref(0)
const total = ref(0)
const items = ref<any[]>([])

const { loading, error, load } = useLoader(async () => {
  const query: Record<string, any> = { limit: LIMIT, offset: offset.value, sandbox: filters.sandbox }
  if (filters.q) query.q = filters.q
  if (filters.channel_id) query.channel_id = filters.channel_id
  if (filters.handover) query.handover = true
  const res = await api<{ items: any[]; total: number }>(path('/conversations'), { query })
  items.value = res.items
  total.value = res.total
  return res
})

function reload() {
  offset.value = 0
  load()
}
function contactName(c: any) {
  return c?.name || c?.username || c?.external_id || '—'
}
function initials(c: any) {
  return contactName(c).slice(0, 2).toUpperCase()
}

watch(offset, () => load())
onMounted(() => {
  load()
  loadInfo().catch(() => {})
})
</script>
