<template>
  <div class="card card-body space-y-3">
    <div class="flex flex-wrap items-center gap-3">
      <h2 class="card-title">{{ t('search.status_title') }}</h2>
      <div class="ml-auto flex flex-wrap gap-2">
        <button type="button" class="btn-ghost btn-sm" :disabled="pinging" @click="ping">
          <UiIcon name="refresh" :size="14" /> {{ t('search.ping') }}
        </button>
        <button v-if="canEdit" type="button" class="btn-secondary btn-sm" :disabled="busy" @click="reindex(false)">
          {{ t('search.reindex') }}
        </button>
        <button v-if="canEdit" type="button" class="btn-ghost btn-sm" :disabled="busy" @click="reindex(true)">
          {{ t('search.reindex_all') }}
        </button>
      </div>
    </div>

    <UiState :loading="loading && !status" :error="error" :has-data="!!status" :retry="load">
      <div v-if="status" class="space-y-3">
        <div class="grid gap-3 grid-cols-2 md:grid-cols-5 text-sm">
          <div>
            <div class="muted text-xs">{{ t('search.count.total') }}</div>
            <div class="text-lg font-semibold">{{ num(counts.total) }}</div>
          </div>
          <div>
            <div class="muted text-xs">{{ t('search.count.indexed') }}</div>
            <div class="text-lg font-semibold text-emerald-600">{{ num(counts.indexed) }}</div>
          </div>
          <div>
            <div class="muted text-xs">{{ t('search.count.pending') }}</div>
            <div class="text-lg font-semibold text-amber-600">{{ num(counts.pending) }}</div>
          </div>
          <div>
            <div class="muted text-xs">{{ t('search.count.error') }}</div>
            <div class="text-lg font-semibold" :class="counts.error ? 'text-rose-600' : ''">{{ num(counts.error) }}</div>
          </div>
          <div>
            <div class="muted text-xs">{{ t('search.embed_model') }}</div>
            <div class="code text-xs mt-1 break-all">{{ status.embed_model || t('search.no_embed_model') }}</div>
          </div>
        </div>

        <div class="flex flex-wrap gap-2 text-xs">
          <span :class="status.words ? 'badge-green' : 'badge-gray'">{{ t('search.words') }}: {{ engineText('words') }}</span>
          <span :class="status.vectors ? 'badge-green' : 'badge-gray'">{{ t('search.vectors') }}: {{ engineText('vectors') }}</span>
        </div>

        <p v-if="kind === 'knowledge' && (!status.vectors || !status.embed_model)" class="alert-amber text-sm">
          {{ t('search.knowledge_needs_vectors') }}
        </p>
        <p v-else-if="kind === 'catalog' && !status.words && !(status.vectors && status.embed_model)" class="alert-amber text-sm">
          {{ t('search.catalog_needs_index') }}
        </p>
        <p v-if="counts.other_model" class="alert-amber text-sm">
          {{ t('search.other_model', { n: counts.other_model }) }}
        </p>
      </div>
    </UiState>
  </div>
</template>

<script setup lang="ts">
// Index status of the catalog or the knowledge base + "re-index" (GET/POST …/search/*).
const props = defineProps<{ kind: 'catalog' | 'knowledge'; canEdit: boolean }>()
const emit = defineEmits<{ reindexed: [] }>()
const { t } = useI18n()
const api = useApi()
const { path } = useTenant()
const { num } = useFormat()
const { busy, run } = useAction()

const status = ref<any>(null)
const pinging = ref(false)

const counts = computed(() => (status.value?.[props.kind] || {}) as Record<string, number>)

const { loading, error, load } = useLoader(async () => {
  status.value = await api<any>(path('/search/status'))
  return status.value
})

function engineText(which: 'words' | 'vectors') {
  if (!status.value?.[which]) return t('search.engine_off')
  const p = status.value.ping?.[which]
  if (!p) return t('search.engine_on')
  return p.ok ? t('search.engine_ok') : t('search.engine_error', { error: p.error })
}

async function ping() {
  pinging.value = true
  try {
    status.value = await api<any>(path('/search/status'), { query: { ping: true } })
  } finally {
    pinging.value = false
  }
}

async function reindex(force: boolean) {
  const res = await run(
    () => api(path('/search/reindex'), { method: 'POST', body: { kind: props.kind, force } }),
    t('search.reindex_queued'),
  )
  if (res) {
    emit('reindexed')
    load()
  }
}

defineExpose({ load })
onMounted(load)
</script>
