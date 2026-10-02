<template>
  <div class="space-y-5">
    <div>
      <h1 class="page-title">{{ t('nav.channels') }}</h1>
      <p class="page-sub">{{ t('channels.sub') }}</p>
    </div>

    <UiState :loading="loading" :error="error" :has-data="!!info" :empty="info && !channels.length ? t('channels.empty') : false" :retry="() => load(true)">
      <div class="grid gap-5 lg:grid-cols-4">
        <div class="card p-2 h-fit">
          <button
            v-for="c in channels"
            :key="c.id"
            type="button"
            class="w-full flex items-center gap-2.5 rounded-button px-3 py-2.5 text-left"
            :class="c.id === selectedId ? 'bg-primary/10 text-primary' : 'hover:bg-slate-50 dark:hover:bg-slate-700/40'"
            @click="selectedId = c.id"
          >
            <span class="grid place-items-center w-8 h-8 rounded-full bg-slate-100 dark:bg-slate-700 text-xs font-semibold uppercase">
              {{ c.kind === 'instagram' ? 'IG' : 'API' }}
            </span>
            <span class="flex-1 min-w-0">
              <span class="block truncate font-medium">{{ c.title }}</span>
              <span class="block truncate text-xs muted">{{ c.username ? `@${c.username}` : t(`channels.kind.${c.kind}`) }}</span>
            </span>
            <StatusBadge kind="channel" :value="c.status" />
          </button>
        </div>

        <div v-if="selected" class="lg:col-span-3 card">
          <div class="card-body pb-0">
            <div class="flex flex-wrap items-center gap-2">
              <h2 class="card-title">{{ selected.title }}</h2>
              <StatusBadge kind="channel" :value="selected.status" />
              <span class="badge-gray">{{ t(`channels.kind.${selected.kind}`) }}</span>
            </div>
            <p v-if="selected.status_error" class="alert-red mt-3">{{ selected.status_error }}</p>
            <p v-if="selected.token_expires_at" class="text-xs muted mt-2">{{ t('channels.token_expires', { at: f.dateTime(selected.token_expires_at) }) }}</p>
            <div class="tabs mt-4">
              <button type="button" class="tab" :class="tab === 'scenarios' ? 'tab-active' : ''" @click="tab = 'scenarios'">{{ t('nav.scenarios') }}</button>
              <button type="button" class="tab" :class="tab === 'settings' ? 'tab-active' : ''" @click="tab = 'settings'">{{ t('nav.settings') }}</button>
            </div>
          </div>
          <div class="card-body">
            <ChannelScenarios v-if="tab === 'scenarios'" :key="selected.id" :channel-id="selected.id" :can-edit="can('admin')" />
            <ChannelSettings
              v-else
              :channel="selected"
              :tenant-settings="info.effective_settings"
              :can-edit="can('admin')"
              @saved="load(true)"
            />
          </div>
        </div>
      </div>
    </UiState>
  </div>
</template>

<script setup lang="ts">
const { t } = useI18n()
const f = useFormat()
const { can } = useTenant()
const { info, channels, load: loadInfo } = useTenantInfo()
usePage(() => [{ label: t('nav.channels') }])

const selectedId = ref('')
const tab = ref<'scenarios' | 'settings'>('scenarios')
const selected = computed(() => channels.value.find((c) => c.id === selectedId.value) || null)

const { loading, error, load: loadOnce } = useLoader(() => loadInfo(true))
async function load(force = false) {
  if (force || !info.value) await loadOnce()
  if (!selected.value) selectedId.value = channels.value[0]?.id || ''
}

onMounted(() => load(true))
</script>
