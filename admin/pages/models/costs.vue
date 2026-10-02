<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="page-title">{{ t('nav.costs') }}</h1>
        <p class="page-sub">{{ t('costs.sub') }}</p>
      </div>
      <div class="flex flex-wrap gap-3">
        <div class="w-full sm:w-56">
          <label class="label">{{ t('models.roles.scope') }}</label>
          <select v-model="scope" class="select" @change="load">
            <option v-if="auth.isSuper" value="">{{ t('costs.all_tenants') }}</option>
            <option v-for="x in tenantChoices" :key="x.id" :value="x.id">{{ x.title }}</option>
          </select>
        </div>
        <div class="w-32">
          <label class="label">{{ t('costs.period') }}</label>
          <select v-model.number="days" class="select" @change="load">
            <option v-for="d in [7, 30, 90, 365]" :key="d" :value="d">{{ t('costs.days', { n: d }) }}</option>
          </select>
        </div>
      </div>
    </div>

    <UiState :loading="loading" :error="error" :has-data="!!data" :retry="load">
      <template v-if="data">
        <div class="grid gap-4 sm:grid-cols-3">
          <div class="card card-body">
            <p class="text-sm muted">{{ t('costs.today') }}</p>
            <p class="text-2xl font-bold mt-1">{{ f.money(data.today) }}</p>
          </div>
          <div class="card card-body">
            <p class="text-sm muted">{{ t('costs.month') }}</p>
            <p class="text-2xl font-bold mt-1">{{ f.money(data.month) }}</p>
          </div>
          <div class="card card-body">
            <p class="text-sm muted">{{ t('costs.in_period', { n: data.days }) }}</p>
            <p class="text-2xl font-bold mt-1">{{ f.money(data.period) }}</p>
          </div>
        </div>

        <div class="card card-body">
          <h2 class="card-title mb-4">{{ t('costs.per_day') }}</h2>
          <div v-if="data.per_day.length" class="flex items-end gap-1 h-40 overflow-x-auto">
            <div
              v-for="d in data.per_day"
              :key="d.date"
              class="flex-1 min-w-[6px] rounded-t bg-primary/70 hover:bg-primary"
              :style="{ height: `${Math.max(2, (Number(d.cost) / maxDay) * 100)}%` }"
              :title="`${f.date(d.date)} · ${f.money(d.cost)}`"
            />
          </div>
          <p v-else class="text-sm muted">{{ t('costs.no_data') }}</p>
          <p class="hint mt-2">{{ t('costs.utc_hint') }}</p>
        </div>

        <div class="grid gap-5 xl:grid-cols-2">
          <div class="card overflow-hidden">
            <div class="card-body pb-0"><h2 class="card-title">{{ t('costs.llm') }}</h2></div>
            <div class="overflow-x-auto">
              <table class="table">
                <thead>
                  <tr>
                    <th>{{ t('models.model') }}</th>
                    <th>{{ t('costs.role') }}</th>
                    <th class="text-right">{{ t('costs.calls') }}</th>
                    <th class="text-right">{{ t('costs.tokens') }}</th>
                    <th class="text-right">{{ t('costs.cost') }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="r in sortedLlm" :key="`${r.model}:${r.role}`">
                    <td class="code text-xs">{{ r.model }}</td>
                    <td class="code text-xs">{{ r.role }}</td>
                    <td class="text-right">{{ f.num(r.calls) }}</td>
                    <td class="text-right text-xs whitespace-nowrap">{{ f.num(r.tokens_in) }} / {{ f.num(r.tokens_out) }}</td>
                    <td class="text-right whitespace-nowrap">{{ f.money(r.cost) }}</td>
                  </tr>
                  <tr v-if="!sortedLlm.length"><td colspan="5" class="text-center muted">{{ t('costs.no_data') }}</td></tr>
                </tbody>
              </table>
            </div>
          </div>

          <div class="card overflow-hidden">
            <div class="card-body pb-0"><h2 class="card-title">{{ t('costs.decisions') }}</h2></div>
            <div class="overflow-x-auto">
              <table class="table">
                <thead>
                  <tr>
                    <th>{{ t('trace.provider') }}</th>
                    <th>{{ t('models.model') }}</th>
                    <th class="text-right">{{ t('costs.questions') }}</th>
                    <th class="text-right">{{ t('costs.requests') }}</th>
                    <th class="text-right">{{ t('costs.cost') }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="r in data.decisions" :key="`${r.provider}:${r.model}`">
                    <td>{{ r.provider }}</td>
                    <td class="code text-xs">{{ r.model || '—' }}</td>
                    <td class="text-right">{{ f.num(r.questions) }}</td>
                    <td class="text-right">{{ f.num(r.requests) }}</td>
                    <td class="text-right whitespace-nowrap">{{ f.money(r.cost) }}</td>
                  </tr>
                  <tr v-if="!data.decisions.length"><td colspan="5" class="text-center muted">{{ t('costs.no_data') }}</td></tr>
                </tbody>
              </table>
            </div>
            <p class="hint px-5 pb-4">{{ t('costs.decisions_hint') }}</p>
          </div>
        </div>
      </template>
    </UiState>
  </div>
</template>

<script setup lang="ts">
import { useAuthStore } from '~/stores/auth'

const { t } = useI18n()
const api = useApi()
const f = useFormat()
const auth = useAuthStore()
const { tenant } = useTenant()
usePage(() => [{ label: t('nav.models'), to: '/models' }, { label: t('nav.costs') }])

const tenantChoices = computed(() => auth.tenants.filter((x) => auth.can(x.id, 'admin')))
const scope = ref<string>(
  tenant.value && auth.can(tenant.value.id, 'admin') ? tenant.value.id : auth.isSuper ? '' : tenantChoices.value[0]?.id || '',
)
const days = ref(30)

const { data, loading, error, load } = useLoader(() =>
  api<any>('/costs', { query: { days: days.value, ...(scope.value ? { tenant_id: scope.value } : {}) } }),
)
const maxDay = computed(() => Math.max(...(data.value?.per_day || []).map((d: any) => Number(d.cost)), 0.000001))
const sortedLlm = computed(() => [...(data.value?.by_model_role || [])].sort((a, b) => Number(b.cost) - Number(a.cost)))

onMounted(load)
</script>
