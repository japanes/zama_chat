<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="page-title">{{ t('nav.roles') }}</h1>
        <p class="page-sub">{{ t('models.roles.sub') }}</p>
      </div>
      <div v-if="tenantChoices.length" class="w-full sm:w-64">
        <label class="label">{{ t('models.roles.scope') }}</label>
        <select v-model="scope" class="select" @change="load">
          <option value="">{{ t('models.roles.shared') }}</option>
          <option v-for="x in tenantChoices" :key="x.id" :value="x.id">{{ x.title }}</option>
        </select>
      </div>
    </div>

    <p v-if="problems.length" class="alert-red">
      {{ t('models.roles.problem_banner') }}
      <span class="code">{{ problems.join(', ') }}</span>
    </p>
    <p class="alert-blue">{{ scope ? t('models.roles.tenant_hint') : t('models.roles.shared_hint') }}</p>

    <UiState :loading="loading" :error="error" :has-data="items.length > 0" :retry="load">
      <div class="space-y-3">
        <div v-for="r in items" :key="r.role" class="card card-body" :class="r.problem ? 'ring-1 ring-rose-300 dark:ring-rose-500/40' : ''">
          <div class="flex flex-wrap items-center gap-2 mb-3">
            <h2 class="card-title code">{{ r.role }}</h2>
            <span class="text-sm muted">{{ t(`models.role.${r.role}`) }}</span>
            <span v-if="r.needs_json_schema" class="badge-gray">{{ t('models.json_schema') }}</span>
            <span v-if="r.problem" class="badge-red">{{ codeText({ code: r.problem, params: { role: r.role, model: current(r)?.model } }) }}</span>
            <span v-else-if="scope && r.effective === 'own'" class="badge-violet ml-auto">{{ t('models.roles.own') }}</span>
            <span v-else-if="scope && r.effective === 'shared'" class="badge-gray ml-auto">{{ t('models.roles.uses_shared') }}</span>
          </div>

          <form class="grid gap-3 sm:grid-cols-2 lg:grid-cols-5 items-end" @submit.prevent="save(r)">
            <div class="lg:col-span-2">
              <label class="label">{{ t('models.model') }}</label>
              <select v-model="forms[r.role].model" class="select" :disabled="!canEdit">
                <option value="">—</option>
                <option v-for="m in suitable(r)" :key="m.name" :value="m.name">{{ m.label || m.name }}</option>
              </select>
            </div>
            <div v-if="modelOf(forms[r.role].model)?.supports_reasoning">
              <label class="label">{{ t('sandbox.reasoning') }}</label>
              <select v-model="forms[r.role].reasoning_effort" class="select" :disabled="!canEdit">
                <option value="">{{ t('models.effort_off') }}</option>
                <option v-for="e in modelOf(forms[r.role].model)?.reasoning_efforts || []" :key="e" :value="e">{{ e }}</option>
              </select>
            </div>
            <div v-else class="hidden lg:block" />
            <div>
              <label class="label">{{ t('models.roles.timeout') }}</label>
              <input v-model.number="forms[r.role].timeout_s" type="number" min="1" max="600" class="input" :readonly="!canEdit" />
            </div>
            <div>
              <label class="label">{{ t('models.roles.fallback') }}</label>
              <select v-model="forms[r.role].fallback" class="select" :disabled="!canEdit">
                <option value="">—</option>
                <option v-for="m in suitable(r).filter((x) => x.name !== forms[r.role].model)" :key="m.name" :value="m.name">{{ m.label || m.name }}</option>
              </select>
            </div>
            <div v-if="canEdit" class="sm:col-span-2 lg:col-span-5 flex flex-wrap justify-end gap-2">
              <button v-if="scope && r.own" type="button" class="btn-secondary btn-sm" :disabled="busy" @click="reset(r)">
                <UiIcon name="rollback" :size="14" /> {{ t('models.roles.reset') }}
              </button>
              <button type="submit" class="btn-primary btn-sm" :disabled="busy || !forms[r.role].model">{{ t('common.save') }}</button>
            </div>
          </form>
          <p v-if="scope && r.shared && r.effective !== 'shared'" class="hint mt-2">
            {{ t('models.roles.shared_is', { model: r.shared.model_label || r.shared.model }) }}
          </p>
        </div>
      </div>
    </UiState>
  </div>
</template>

<script setup lang="ts">
import { useAuthStore } from '~/stores/auth'

const { t } = useI18n()
const api = useApi()
const toast = useToast()
const confirm = useConfirm()
const auth = useAuthStore()
const { errorText, codeText } = useErrorText()
const { tenant } = useTenant()
usePage(() => [{ label: t('nav.models'), to: '/models' }, { label: t('nav.roles') }])

// tenants where the user may set its own models
const tenantChoices = computed(() => auth.tenants.filter((x) => auth.can(x.id, 'admin')))
const scope = ref<string>(tenant.value && auth.can(tenant.value.id, 'admin') ? tenant.value.id : '')
const canEdit = computed(() => (scope.value ? auth.can(scope.value, 'admin') : auth.isSuper))

const items = ref<any[]>([])
const models = ref<any[]>([])
const forms = reactive<Record<string, any>>({})
const busy = ref(false)
const problems = computed(() => items.value.filter((r) => r.problem).map((r) => r.role))

function current(r: any) {
  return r.effective === 'own' ? r.own : r.shared
}

const { loading, error, load } = useLoader(async () => {
  const [roles, all] = await Promise.all([
    api<{ items: any[] }>('/roles', { query: scope.value ? { tenant_id: scope.value } : {} }),
    api<{ items: any[] }>('/models'),
  ])
  models.value = all.items
  items.value = roles.items
  for (const r of roles.items) {
    // the form shows the row of this scope; a tenant without its own row starts from the shared one
    const a = (scope.value ? r.own || r.shared : r.shared) || {}
    forms[r.role] = {
      model: a.model || '',
      reasoning_effort: a.reasoning_effort || '',
      timeout_s: a.timeout_s ?? 60,
      fallback: a.fallback || '',
    }
  }
  refreshRoleProblems(scope.value || null, true)
  return roles
})

function modelOf(name: string) {
  return models.value.find((m) => m.name === name) || null
}
// the server checks it too; the list only offers what can work
function suitable(r: any) {
  return models.value.filter((m) => {
    if (!m.is_active) return false
    if (r.role === 'embed') return m.kind === 'embedding'
    if (m.kind !== 'language') return false
    if (r.needs_json_schema && !m.supports_json_schema) return false
    if (r.role === 'vision' && !m.supports_vision) return false
    return true
  })
}

async function save(r: any) {
  const f = forms[r.role]
  busy.value = true
  try {
    await api(`/roles/${r.role}`, {
      method: 'PUT',
      body: {
        tenant_id: scope.value || null,
        model: f.model,
        reasoning_effort: modelOf(f.model)?.supports_reasoning ? f.reasoning_effort || null : null,
        timeout_s: f.timeout_s || null,
        fallback: f.fallback || null,
      },
    })
    toast.ok(t('models.roles.saved', { role: r.role }))
    await load()
  } catch (e) {
    toast.error(errorText(e))
  } finally {
    busy.value = false
  }
}

async function reset(r: any) {
  if (!(await confirm.confirm({ message: t('models.roles.reset_confirm', { role: r.role }) }))) return
  busy.value = true
  try {
    await api(`/roles/${r.role}`, { method: 'DELETE', query: { tenant_id: scope.value } })
    toast.ok(t('models.roles.reset_done'))
    await load()
  } catch (e) {
    toast.error(errorText(e))
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>
