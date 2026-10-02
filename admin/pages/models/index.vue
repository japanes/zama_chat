<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="page-title">{{ t('nav.models') }}</h1>
        <p class="page-sub">{{ t('models.sub') }}</p>
      </div>
      <button v-if="auth.isSuper" type="button" class="btn-primary" @click="openAdd">
        <UiIcon name="plus" :size="16" /> {{ t('models.add') }}
      </button>
    </div>

    <div class="card overflow-hidden">
      <UiState :loading="loading" :error="error" :empty="!loading && !items.length ? t('models.empty') : false" :has-data="items.length > 0" :retry="load">
        <div class="overflow-x-auto">
          <table class="table">
            <thead>
              <tr>
                <th>{{ t('models.model') }}</th>
                <th class="text-right">{{ t('models.price_input_short') }}</th>
                <th class="text-right">{{ t('models.price_cached_short') }}</th>
                <th class="text-right">{{ t('models.price_output_short') }}</th>
                <th>{{ t('models.capabilities') }}</th>
                <th>{{ t('models.used_in') }}</th>
                <th v-if="auth.isSuper" />
              </tr>
            </thead>
            <tbody>
              <tr v-for="m in items" :key="m.id" :class="m.is_active ? '' : 'opacity-60'">
                <td>
                  <p class="font-medium">{{ m.label || m.name }}</p>
                  <p class="code text-xs muted">{{ m.name }}</p>
                  <span v-if="m.kind === 'embedding'" class="badge-violet mt-1">{{ t('models.kinds.embedding') }}</span>
                  <span v-if="!m.is_active" class="badge-gray mt-1 ml-1">{{ t('models.off') }}</span>
                </td>
                <td class="text-right whitespace-nowrap">{{ price(m.price_input) }}</td>
                <td class="text-right whitespace-nowrap">{{ price(m.price_cached_input) }}</td>
                <td class="text-right whitespace-nowrap">{{ price(m.price_output) }}</td>
                <td>
                  <div class="flex flex-wrap gap-1">
                    <span v-if="m.supports_json_schema" class="badge-blue">{{ t('models.json_schema') }}</span>
                    <span v-if="m.supports_reasoning" class="badge-blue" :title="(m.reasoning_efforts || []).join(', ')">{{ t('models.reasoning') }}</span>
                    <span v-if="m.supports_vision" class="badge-blue">{{ t('models.vision') }}</span>
                    <span v-if="m.context_window" class="badge-gray">{{ f.num(m.context_window / 1000) }}k</span>
                  </div>
                </td>
                <td>
                  <div class="flex flex-wrap gap-1">
                    <span v-for="r in m.roles" :key="r" class="badge-green code">{{ r }}</span>
                    <span v-if="!m.roles.length" class="text-xs muted">—</span>
                  </div>
                </td>
                <td v-if="auth.isSuper" class="text-right whitespace-nowrap">
                  <button type="button" class="btn-icon btn-ghost btn-sm" :aria-label="t('common.edit')" @click="openEdit(m)">
                    <UiIcon name="edit" :size="16" />
                  </button>
                  <button type="button" class="btn-icon btn-ghost btn-sm text-rose-600" :disabled="m.roles.length > 0" :title="m.roles.length ? t('models.in_use_hint') : ''" :aria-label="t('common.delete')" @click="remove(m)">
                    <UiIcon name="trash" :size="16" />
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </UiState>
    </div>
    <p class="hint">{{ t('models.prices_hint') }}</p>

    <!-- add: from the Gateway catalog (prices filled in) or by hand -->
    <UiModal :open="addOpen" :title="t('models.add')" size="lg" @close="addOpen = false">
      <div class="tabs mb-4">
        <button type="button" class="tab" :class="addMode === 'catalog' ? 'tab-active' : ''" @click="addMode = 'catalog'">{{ t('models.from_catalog') }}</button>
        <button type="button" class="tab" :class="addMode === 'manual' ? 'tab-active' : ''" @click="startManual">{{ t('models.manual') }}</button>
      </div>

      <div v-if="addMode === 'catalog' && !draft" class="space-y-3">
        <div class="flex gap-2">
          <input v-model.trim="catalogQuery" class="input flex-1" :placeholder="t('models.catalog_search')" />
          <select v-model="catalogKind" class="select w-40">
            <option value="language">{{ t('models.kinds.language') }}</option>
            <option value="embedding">{{ t('models.kinds.embedding') }}</option>
          </select>
        </div>
        <UiState :loading="catalogLoading" :error="catalogError" :empty="!catalogLoading && !catalogShown.length" :has-data="catalogShown.length > 0" :retry="loadCatalog">
          <ul class="max-h-96 overflow-y-auto divide-y divide-slate-100 dark:divide-slate-700 rounded-button border border-slate-200 dark:border-slate-700">
            <li v-for="c in catalogShown" :key="c.name">
              <button type="button" class="w-full text-left px-3 py-2.5 hover:bg-slate-50 dark:hover:bg-slate-700/40 disabled:opacity-50" :disabled="c.added" @click="pick(c)">
                <div class="flex items-center gap-2">
                  <span class="font-medium">{{ c.label }}</span>
                  <span class="code text-xs muted">{{ c.name }}</span>
                  <span v-if="c.added" class="badge-green ml-auto">{{ t('models.added') }}</span>
                </div>
                <p class="text-xs muted mt-0.5">
                  {{ price(c.price_input) }} / {{ price(c.price_output) }}
                  <template v-if="c.context_window"> · {{ f.num(c.context_window / 1000) }}k</template>
                  <template v-if="c.supports_json_schema"> · {{ t('models.json_schema') }}</template>
                  <template v-if="c.supports_reasoning"> · {{ t('models.reasoning') }}</template>
                </p>
              </button>
            </li>
          </ul>
        </UiState>
      </div>

      <form v-if="draft" id="model-add" class="space-y-5" @submit.prevent="saveNew">
        <ModelForm :model="draft" :name-locked="addMode === 'catalog'" />
        <ModelCheck :name="draft.name" :kind="draft.kind" :efforts="draft.supports_reasoning ? draft.reasoning_efforts : []" />
        <p v-if="formError" class="alert-red">{{ formError }}</p>
      </form>

      <template v-if="draft" #footer>
        <button type="button" class="btn-secondary" @click="addMode === 'catalog' ? (draft = null) : (addOpen = false)">
          {{ addMode === 'catalog' ? t('common.back') : t('common.cancel') }}
        </button>
        <button type="submit" form="model-add" class="btn-primary" :disabled="busy">{{ t('common.save') }}</button>
      </template>
    </UiModal>

    <UiModal :open="!!editing" :title="editing?.name" size="lg" @close="editing = null">
      <form v-if="editing" id="model-edit" class="space-y-5" @submit.prevent="saveEdit">
        <ModelForm :model="editing" name-locked />
        <ModelCheck :name="editing.name" :kind="editing.kind" :efforts="editing.supports_reasoning ? editing.reasoning_efforts : []" />
        <p v-if="formError" class="alert-red">{{ formError }}</p>
      </form>
      <template #footer>
        <button type="button" class="btn-secondary" @click="editing = null">{{ t('common.cancel') }}</button>
        <button type="submit" form="model-edit" class="btn-primary" :disabled="busy">{{ t('common.save') }}</button>
      </template>
    </UiModal>
  </div>
</template>

<script setup lang="ts">
import { useAuthStore } from '~/stores/auth'

const { t } = useI18n()
const api = useApi()
const f = useFormat()
const toast = useToast()
const confirm = useConfirm()
const auth = useAuthStore()
const { errorText } = useErrorText()
const { run } = useAction()
const { tenantId } = useTenant()
usePage(() => [{ label: t('nav.models') }])

const FIELDS = [
  'label',
  'kind',
  'price_input',
  'price_cached_input',
  'price_output',
  'context_window',
  'max_output',
  'supports_json_schema',
  'supports_reasoning',
  'supports_vision',
  'reasoning_efforts',
  'is_active',
]

const items = ref<any[]>([])
const addOpen = ref(false)
const addMode = ref<'catalog' | 'manual'>('catalog')
const draft = ref<any>(null)
const editing = ref<any>(null)
const busy = ref(false)
const formError = ref<string | null>(null)
const catalog = ref<any[]>([])
const catalogQuery = ref('')
const catalogKind = ref('language')
const catalogLoading = ref(false)
const catalogError = ref<string | null>(null)

const catalogShown = computed(() => {
  const q = catalogQuery.value.toLowerCase()
  return catalog.value
    .filter((c) => c.kind === catalogKind.value)
    .filter((c) => !q || c.name.toLowerCase().includes(q) || (c.label || '').toLowerCase().includes(q))
    .slice(0, 200)
})

const { loading, error, load } = useLoader(async () => {
  items.value = (await api<{ items: any[] }>('/models')).items
  return items.value
})

function price(v: string | null) {
  return v == null ? '—' : `$${Number(v)}`
}

async function loadCatalog() {
  catalogLoading.value = true
  catalogError.value = null
  try {
    catalog.value = (await api<{ items: any[] }>('/models/catalog')).items
  } catch (e) {
    catalogError.value = errorText(e)
  } finally {
    catalogLoading.value = false
  }
}

function openAdd() {
  addMode.value = 'catalog'
  draft.value = null
  formError.value = null
  addOpen.value = true
  if (!catalog.value.length) loadCatalog()
}
function blank() {
  return {
    name: '',
    label: '',
    kind: 'language',
    price_input: '0',
    price_cached_input: null,
    price_output: '0',
    context_window: null,
    max_output: null,
    supports_json_schema: true,
    supports_reasoning: false,
    supports_vision: false,
    reasoning_efforts: [],
    is_active: true,
  }
}
function startManual() {
  addMode.value = 'manual'
  draft.value = blank()
}
function pick(c: any) {
  draft.value = { ...blank(), ...c, kind: c.kind === 'embedding' ? 'embedding' : 'language', is_active: true }
}

// empty inputs → null (a nullable price / limit is cleared)
function fields(m: any) {
  const out: Record<string, any> = {}
  for (const k of FIELDS) out[k] = m[k] === '' ? null : m[k]
  return out
}

async function saveNew() {
  busy.value = true
  formError.value = null
  try {
    // from the catalog: the server takes the catalog values, the sent ones win
    await api('/models', { method: 'POST', body: { name: draft.value.name, from_gateway: addMode.value === 'catalog', ...fields(draft.value) } })
    addOpen.value = false
    toast.ok(t('models.saved'))
    catalog.value = []
    load()
  } catch (e) {
    formError.value = errorText(e)
  } finally {
    busy.value = false
  }
}

function openEdit(m: any) {
  formError.value = null
  editing.value = JSON.parse(JSON.stringify(m))
}
async function saveEdit() {
  busy.value = true
  formError.value = null
  try {
    await api(`/models/${editing.value.id}`, { method: 'PATCH', body: fields(editing.value) })
    editing.value = null
    toast.ok(t('models.saved'))
    load()
    refreshRoleProblems(tenantId.value || null, true)
  } catch (e) {
    formError.value = errorText(e)
  } finally {
    busy.value = false
  }
}

async function remove(m: any) {
  if (!(await confirm.confirm({ message: t('models.remove_confirm', { name: m.name }), tone: 'danger', confirmLabel: t('common.delete') }))) return
  const res = await run(() => api(`/models/${m.id}`, { method: 'DELETE' }), t('models.removed'))
  if (res) load()
}

onMounted(load)
</script>
