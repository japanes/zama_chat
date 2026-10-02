<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="page-title">{{ t('nav.scenarios') }}</h1>
        <p class="page-sub">{{ t('scenarios.sub') }}</p>
      </div>
      <NuxtLink v-if="can('admin')" :to="`${base}/scenarios/new`" class="btn-primary">
        <UiIcon name="plus" :size="16" /> {{ t('scenarios.new') }}
      </NuxtLink>
    </div>

    <div class="card overflow-hidden">
      <UiState :loading="loading" :error="error" :empty="!loading && !items.length ? t('scenarios.empty') : false" :has-data="items.length > 0" :retry="load">
        <div class="overflow-x-auto">
          <table class="table table-hover">
            <thead>
              <tr>
                <th>{{ t('scenarios.title') }}</th>
                <th>{{ t('scenarios.version') }}</th>
                <th>{{ t('nav.channels') }}</th>
                <th>{{ t('scenarios.updated') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="s in items" :key="s.key" @click="navigateTo(`${base}/scenarios/${s.key}`)">
                <td>
                  <span class="font-medium">{{ s.title || s.key }}</span>
                  <p class="code text-xs muted">{{ s.key }}</p>
                </td>
                <td class="whitespace-nowrap">
                  <span v-if="s.active_version" class="badge-green">v{{ s.active_version }}</span>
                  <span v-else class="badge-gray">{{ t('scenarios.off') }}</span>
                  <span v-if="s.latest_version !== s.active_version" class="text-xs muted ml-1">{{ t('scenarios.latest', { v: s.latest_version }) }}</span>
                </td>
                <td>
                  <div class="flex flex-wrap gap-1">
                    <span v-for="c in s.channels" :key="c.channel_id" :class="c.is_enabled ? 'badge-blue' : 'badge-gray'">
                      {{ c.title }} #{{ c.position + 1 }}
                    </span>
                    <span v-if="!s.channels.length" class="text-xs muted">{{ t('scenarios.no_channels') }}</span>
                  </div>
                </td>
                <td class="whitespace-nowrap text-sm muted">{{ f.dateTime(s.updated_at) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </UiState>
    </div>
  </div>
</template>

<script setup lang="ts">
const { t } = useI18n()
const api = useApi()
const f = useFormat()
const { base, path, can } = useTenant()
usePage(() => [{ label: t('nav.scenarios') }])

const items = ref<any[]>([])
const { loading, error, load } = useLoader(async () => {
  items.value = (await api<{ items: any[] }>(path('/scenarios'))).items
  return items.value
})
onMounted(load)
</script>
