<template>
  <!-- every prompt key; for a tenant: whether it uses the shared prompt or its own -->
  <div class="card overflow-hidden">
    <UiState :loading="loading" :error="error" :empty="!loading && !items.length ? t('prompts.empty') : false" :has-data="items.length > 0" :retry="load">
      <div class="overflow-x-auto">
        <table class="table table-hover">
          <thead>
            <tr>
              <th>{{ t('prompts.key') }}</th>
              <th>{{ t('prompts.shared') }}</th>
              <th v-if="tenantId">{{ t('prompts.own') }}</th>
              <th>{{ t('prompts.used') }}</th>
              <th>{{ t('prompts.updated') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in items" :key="p.key" @click="navigateTo(`${linkBase}/${p.key}`)">
              <td class="code font-medium">{{ p.key }}</td>
              <td>
                <span v-if="p.shared" class="text-sm">v{{ p.shared.active ?? '—' }} <span class="muted text-xs">/ {{ p.shared.versions }}</span></span>
                <span v-else class="muted">—</span>
              </td>
              <td v-if="tenantId">
                <span v-if="p.own" class="text-sm">v{{ p.own.active ?? '—' }} <span class="muted text-xs">/ {{ p.own.versions }}</span></span>
                <span v-else class="muted">—</span>
              </td>
              <td>
                <span v-if="p.effective === 'own'" class="badge-violet">{{ t('prompts.own') }}</span>
                <span v-else-if="p.effective === 'shared'" class="badge-gray">{{ t('prompts.shared') }}</span>
                <span v-else class="badge-red">{{ t('prompts.none') }}</span>
              </td>
              <td class="text-sm muted whitespace-nowrap">{{ f.dateTime(p.own?.updated_at || p.shared?.updated_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </UiState>
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{ tenantId: string | null; linkBase: string }>()
const { t } = useI18n()
const api = useApi()
const f = useFormat()

const items = ref<any[]>([])
const { loading, error, load } = useLoader(async () => {
  const query = props.tenantId ? { tenant_id: props.tenantId } : {}
  items.value = (await api<{ items: any[] }>('/prompts', { query })).items
  return items.value
})
onMounted(load)
</script>
