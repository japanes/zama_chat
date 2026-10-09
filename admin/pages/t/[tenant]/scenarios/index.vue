<template>
  <div class="max-w-[980px] mx-auto space-y-5">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div class="min-w-0">
        <h1 class="page-title">{{ t('nav.scenarios') }}</h1>
        <p class="page-sub">{{ t('editor.list.sub') }}</p>
      </div>
      <NuxtLink v-if="can('admin')" :to="`${base}/scenarios/new`" class="btn-primary">
        <UiIcon name="plus" :size="16" /> {{ t('scenarios.new') }}
      </NuxtLink>
    </div>

    <UiState :loading="loading" :error="error" :has-data="loaded" :retry="load">
      <ul v-if="items.length" class="space-y-2.5">
        <li v-for="s in items" :key="s.key">
          <NuxtLink :to="`${base}/scenarios/${s.key}`" class="card card-hover flex flex-wrap items-center gap-x-4 gap-y-1.5 px-5 py-4 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary">
            <span class="flex-1 basis-[260px] min-w-0">
              <span class="block text-[17px] font-semibold text-slate-900 dark:text-white truncate">{{ s.title || s.key }}</span>
              <span class="block text-sm truncate" :class="where(s) ? 'muted' : 'text-[#7a4b00] dark:text-amber-300'">
                {{ where(s) ? t('editor.list.channels', { list: where(s) }) : t('editor.list.no_channels') }}
              </span>
            </span>
            <span v-if="s.active_version" class="badge-green">{{ t('editor.status.on') }}</span>
            <span v-else class="badge-gray">{{ t('editor.status.off') }}</span>
            <span class="text-sm muted whitespace-nowrap">{{ t('editor.list.changed', { when: f.relative(s.updated_at) }) }}</span>
            <UiIcon name="chevron-right" :size="18" class="muted shrink-0" />
          </NuxtLink>
        </li>
      </ul>
      <div v-else-if="loaded" class="card card-body text-center py-10">
        <p class="text-[17px] font-semibold text-slate-900 dark:text-white">{{ t('scenarios.empty') }}</p>
        <p class="mt-1 muted">{{ t('editor.list.empty_sub') }}</p>
        <NuxtLink v-if="can('admin')" :to="`${base}/scenarios/new`" class="btn-primary mt-5">{{ t('editor.list.first') }}</NuxtLink>
      </div>
    </UiState>
  </div>
</template>

<script setup lang="ts">
const { t } = useI18n()
const api = useApi()
const f = useFormat()
const { base, path, can } = useTenant()
usePage(() => [{ label: t('nav.scenarios') }])

const items = ref<any[]>([])
const loaded = ref(false)
const { loading, error, load } = useLoader(async () => {
  items.value = (await api<{ items: any[] }>(path('/scenarios'))).items
  loaded.value = true
  return items.value
})

// the channels the scenario is switched on in, as one line
function where(s: any): string {
  return (s.channels as any[])
    .filter((c) => c.is_enabled)
    .map((c) => c.title)
    .join(', ')
}
onMounted(load)
</script>
