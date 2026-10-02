<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="page-title">{{ t('nav.operator') }}</h1>
        <p class="page-sub">{{ t('operator.sub') }}</p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <span class="text-xs muted">{{ t('operator.auto_refresh') }}</span>
        <button type="button" class="btn-secondary btn-sm" :disabled="loading" @click="load">
          <UiIcon name="refresh" :size="14" /> {{ t('common.refresh') }}
        </button>
      </div>
    </div>

    <div class="tabs">
      <button
        v-for="k in KINDS"
        :key="k"
        type="button"
        class="tab"
        :class="kind === k ? 'tab-active' : ''"
        @click="kind = k"
      >
        {{ k === 'all' ? t('common.all') : t(`operator.kind.${k}`) }}
        <span v-if="count(k)" class="ml-1 badge-gray">{{ count(k) }}</span>
      </button>
    </div>

    <UiState :loading="loading && !data" :error="error" :has-data="!!data" :empty="data && !shown.length ? t('operator.empty') : false" :retry="load">
      <div class="grid gap-4 xl:grid-cols-2">
        <TaskCard
          v-for="task in shown"
          :key="task.id"
          :task="task"
          with-contact
          can-act
          @resolved="load"
        />
      </div>
    </UiState>
  </div>
</template>

<script setup lang="ts">
const { t } = useI18n()
const api = useApi()
const { path } = useTenant()
usePage(() => [{ label: t('nav.operator') }])

const KINDS = ['all', 'choose_reply', 'handover'] as const
const POLL_MS = 5000
const kind = ref<(typeof KINDS)[number]>('all')

const { data, loading, error, load } = useLoader(() => api<any>(path('/operator/tasks')))
const shown = computed(() =>
  ((data.value?.items || []) as any[]).filter((x) => kind.value === 'all' || x.kind === kind.value),
)
function count(k: string) {
  const open = data.value?.open || {}
  return k === 'all' ? Object.values(open).reduce((a: number, b: any) => a + Number(b), 0) : open[k] || 0
}

// polling, no sockets: quiet refresh while the tab is visible
let timer: ReturnType<typeof setInterval> | null = null
onMounted(() => {
  load()
  timer = setInterval(() => {
    if (!document.hidden && !loading.value) load()
  }, POLL_MS)
})
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})
</script>
