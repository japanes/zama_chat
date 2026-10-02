<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="page-title">{{ t('nav.api_keys') }}</h1>
        <p class="page-sub">{{ t('api_keys.sub') }}</p>
      </div>
      <button type="button" class="btn-primary" @click="openAdd">
        <UiIcon name="plus" :size="16" /> {{ t('api_keys.create') }}
      </button>
    </div>

    <div class="card overflow-hidden">
      <UiState :loading="loading" :error="error" :empty="!loading && !items.length ? t('api_keys.empty') : false" :has-data="items.length > 0" :retry="load">
        <div class="overflow-x-auto">
          <table class="table">
            <thead>
              <tr>
                <th>{{ t('api_keys.title') }}</th>
                <th>{{ t('api_keys.prefix') }}</th>
                <th>{{ t('results.channel') }}</th>
                <th>{{ t('api_keys.created') }}</th>
                <th>{{ t('api_keys.last_used') }}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              <tr v-for="k in items" :key="k.id" :class="k.revoked_at ? 'opacity-60' : ''">
                <td class="font-medium">{{ k.title || '—' }}</td>
                <td class="code">{{ k.prefix }}…</td>
                <td>{{ k.channel_title }}</td>
                <td class="text-sm muted whitespace-nowrap">{{ f.dateTime(k.created_at) }}</td>
                <td class="text-sm muted whitespace-nowrap">{{ f.dateTime(k.last_used_at) }}</td>
                <td class="text-right">
                  <span v-if="k.revoked_at" class="badge-gray">{{ t('api_keys.revoked') }}</span>
                  <button v-else type="button" class="btn-danger btn-sm" @click="revoke(k)">{{ t('api_keys.revoke') }}</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </UiState>
    </div>

    <div class="card card-body text-sm space-y-2">
      <h2 class="card-title">{{ t('api_keys.how_title') }}</h2>
      <p class="muted">{{ t('api_keys.how_text') }}</p>
      <pre class="pre">curl -X POST {{ origin }}/v1/messages \
  -H "Authorization: Bearer &lt;key&gt;" \
  -H "Content-Type: application/json" \
  -d '{"contact": {"external_id": "client-1"}, "text": "Hello"}'</pre>
    </div>

    <UiModal :open="addOpen" :title="t('api_keys.create')" @close="addOpen = false">
      <form id="key-form" class="space-y-4" @submit.prevent="create">
        <div>
          <label class="label">{{ t('api_keys.title') }}</label>
          <input v-model.trim="form.title" class="input" :placeholder="t('api_keys.title_placeholder')" />
        </div>
        <div v-if="httpChannels.length > 1">
          <label class="label">{{ t('results.channel') }}</label>
          <select v-model="form.channel_id" class="select">
            <option v-for="c in httpChannels" :key="c.id" :value="c.id">{{ c.title }}</option>
          </select>
        </div>
      </form>
      <template #footer>
        <button type="button" class="btn-secondary" @click="addOpen = false">{{ t('common.cancel') }}</button>
        <button type="submit" form="key-form" class="btn-primary" :disabled="busy">{{ t('api_keys.create') }}</button>
      </template>
    </UiModal>

    <SecretOnce :value="newKey" :title="t('api_keys.new_key')" @close="newKey = null" />
  </div>
</template>

<script setup lang="ts">
const { t } = useI18n()
const api = useApi()
const f = useFormat()
const confirm = useConfirm()
const { path } = useTenant()
const { channels, load: loadInfo } = useTenantInfo()
const { busy, run } = useAction()
usePage(() => [{ label: t('nav.api_keys') }])

// the public API address is the backend's; in dev the admin and the API differ
const origin = computed(() => (import.meta.client ? window.location.origin.replace(/:3120$/, ':8090') : ''))
const items = ref<any[]>([])
const addOpen = ref(false)
const newKey = ref<string | null>(null)
const form = reactive({ title: '', channel_id: '' })
const httpChannels = computed(() => channels.value.filter((c) => c.kind === 'http'))

const { loading, error, load } = useLoader(async () => {
  items.value = (await api<{ items: any[] }>(path('/api-keys'))).items
  return items.value
})

function openAdd() {
  form.title = ''
  form.channel_id = httpChannels.value[0]?.id || ''
  addOpen.value = true
}
async function create() {
  const res = await run(() =>
    api<any>(path('/api-keys'), { method: 'POST', body: { title: form.title, channel_id: form.channel_id || null } }),
  )
  if (res) {
    addOpen.value = false
    newKey.value = res.key
    load()
  }
}
async function revoke(k: any) {
  if (!(await confirm.confirm({ message: t('api_keys.revoke_confirm', { title: k.title || k.prefix }), tone: 'danger', confirmLabel: t('api_keys.revoke') }))) return
  const res = await run(() => api(path(`/api-keys/${k.id}`), { method: 'DELETE' }), t('api_keys.revoked'))
  if (res) load()
}

onMounted(() => {
  load()
  loadInfo().catch(() => {})
})
</script>
