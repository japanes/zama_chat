<template>
  <!-- who changed what; the log has no total count, so pages go by "is there more" -->
  <div class="card overflow-hidden">
    <UiState :loading="loading" :error="error" :empty="!loading && !items.length" :has-data="items.length > 0" :retry="load">
      <div class="overflow-x-auto">
        <table class="table">
          <thead>
            <tr>
              <th>{{ t('audit.when') }}</th>
              <th>{{ t('audit.who') }}</th>
              <th>{{ t('audit.action') }}</th>
              <th v-if="showTenant">{{ t('audit.tenant') }}</th>
              <th>{{ t('audit.details') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="a in items" :key="a.id">
              <td class="text-sm whitespace-nowrap">{{ f.dateTime(a.created_at) }}</td>
              <td class="text-sm">{{ a.user_email || '—' }}</td>
              <td>
                <span class="font-medium">{{ actionLabel(a.action) }}</span>
                <p v-if="a.target_id" class="code text-xs muted break-all">{{ a.target_type }}: {{ a.target_id }}</p>
              </td>
              <td v-if="showTenant" class="text-sm">{{ tenantTitle(a.tenant_id) }}</td>
              <td class="min-w-[14rem]">
                <JsonView v-if="a.data && Object.keys(a.data).length" :value="a.data" collapsible :label="t('audit.data')" />
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="flex items-center justify-end gap-1 px-3 py-3">
        <button type="button" class="btn-secondary btn-sm" :disabled="offset === 0" @click="go(-1)">
          <UiIcon name="chevron-left" :size="14" />
        </button>
        <button type="button" class="btn-secondary btn-sm" :disabled="items.length < LIMIT" @click="go(1)">
          <UiIcon name="chevron-right" :size="14" />
        </button>
      </div>
    </UiState>
  </div>
</template>

<script setup lang="ts">
import { useAuthStore } from '~/stores/auth'

const props = defineProps<{ tenantId: string | null }>()
const { t, te } = useI18n()
const api = useApi()
const f = useFormat()
const auth = useAuthStore()

const LIMIT = 50
const items = ref<any[]>([])
const offset = ref(0)
const showTenant = computed(() => !props.tenantId)

const { loading, error, load } = useLoader(async () => {
  const query: Record<string, any> = { limit: LIMIT, offset: offset.value }
  if (props.tenantId) query.tenant_id = props.tenantId
  items.value = (await api<{ items: any[] }>('/audit', { query })).items
  return items.value
})

function go(dir: number) {
  offset.value = Math.max(0, offset.value + dir * LIMIT)
  load()
}
function actionLabel(action: string) {
  const key = `audit.actions.${action.replace('.', '_')}`
  return te(key) ? t(key) : action
}
function tenantTitle(id: string | null) {
  if (!id) return '—'
  return auth.tenants.find((x) => x.id === id)?.title || id.slice(0, 8)
}

onMounted(load)
</script>
