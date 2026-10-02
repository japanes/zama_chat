<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="page-title">{{ t('nav.catalog') }}</h1>
        <p class="page-sub">{{ t('catalog.sub') }}</p>
      </div>
      <div v-if="canEdit" class="flex flex-wrap gap-2">
        <button type="button" class="btn-secondary" @click="openImport">
          <UiIcon name="upload" :size="16" /> {{ t('catalog.import') }}
        </button>
        <button type="button" class="btn-primary" @click="openForm(null)">
          <UiIcon name="plus" :size="16" /> {{ t('catalog.add') }}
        </button>
      </div>
    </div>

    <SearchStatus ref="statusBox" kind="catalog" :can-edit="canEdit" @reindexed="load" />

    <div class="card overflow-hidden">
      <div class="flex flex-wrap gap-3 p-3 border-b border-slate-200 dark:border-slate-700">
        <input v-model="filters.q" class="input md:w-64" :placeholder="t('catalog.search_placeholder')" @keyup.enter="reload" />
        <select v-model="filters.category" class="select md:w-56" @change="reload">
          <option value="">{{ t('catalog.all_categories') }}</option>
          <option v-for="c in categories" :key="c.name || '-'" :value="c.name || ''">{{ c.name || t('catalog.no_category') }} ({{ c.count }})</option>
        </select>
        <select v-model="filters.status" class="select md:w-48" @change="reload">
          <option value="">{{ t('catalog.all_statuses') }}</option>
          <option v-for="s in STATUSES" :key="s" :value="s">{{ t(`status.index.${s}`) }}</option>
        </select>
      </div>
      <UiState :loading="loading" :error="error" :empty="!loading && !items.length ? t('catalog.empty') : false" :has-data="items.length > 0" :retry="load">
        <div class="overflow-x-auto">
          <table class="table table-hover">
            <thead>
              <tr>
                <th>{{ t('catalog.title') }}</th>
                <th>{{ t('catalog.category') }}</th>
                <th>{{ t('catalog.price') }}</th>
                <th>{{ t('catalog.index') }}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in items" :key="item.id">
                <td>
                  <div class="font-medium">{{ item.title }}</div>
                  <div class="text-xs muted code">{{ item.external_id }} · {{ item.language }}</div>
                </td>
                <td class="text-sm">{{ item.category || '—' }}</td>
                <td class="text-sm whitespace-nowrap">{{ item.price ? `${num(item.price, 2)} ${item.currency || ''}` : '—' }}</td>
                <td>
                  <span v-if="!item.is_active" class="badge-gray">{{ t('status.index.inactive') }}</span>
                  <StatusBadge v-else kind="index" :value="item.index_status" />
                  <div v-if="item.index_error" class="text-xs text-rose-600 mt-1">{{ indexError(item.index_error) }}</div>
                </td>
                <td class="text-right whitespace-nowrap">
                  <button type="button" class="btn-icon btn-ghost btn-sm" :aria-label="canEdit ? t('common.edit') : t('common.open')" @click="openForm(item)">
                    <UiIcon :name="canEdit ? 'edit' : 'external'" :size="16" />
                  </button>
                  <button v-if="canEdit" type="button" class="btn-icon btn-ghost btn-sm text-rose-600" :aria-label="t('common.delete')" @click="remove(item)">
                    <UiIcon name="trash" :size="16" />
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <UiPagination :total="total" :limit="LIMIT" :offset="offset" @update:offset="(v) => { offset = v; load() }" />
      </UiState>
    </div>

    <SearchTest default-kind="catalog" />

    <!-- item form -->
    <UiModal :open="formOpen" :title="editing ? editing.title : t('catalog.add')" size="lg" @close="formOpen = false">
      <form id="item-form" class="space-y-4" @submit.prevent="submit">
        <div class="grid gap-4 md:grid-cols-3">
          <div>
            <label class="label">{{ t('catalog.external_id') }}</label>
            <input v-model.trim="form.external_id" class="input code" required maxlength="200" :readonly="!!editing" />
            <p class="hint">{{ t('catalog.external_id_hint') }}</p>
          </div>
          <div class="md:col-span-2">
            <label class="label">{{ t('catalog.title') }}</label>
            <input v-model="form.title" class="input" required maxlength="300" :readonly="!canEdit" />
          </div>
        </div>
        <div>
          <label class="label">{{ t('catalog.description') }}</label>
          <textarea v-model="form.description" rows="5" class="textarea" maxlength="20000" :readonly="!canEdit" />
          <p class="hint">{{ t('catalog.description_hint') }}</p>
        </div>
        <div class="grid gap-4 md:grid-cols-4">
          <div class="md:col-span-2">
            <label class="label">{{ t('catalog.category') }}</label>
            <input v-model="form.category" class="input" maxlength="200" list="catalog-categories" :readonly="!canEdit" />
            <datalist id="catalog-categories">
              <option v-for="c in categories" :key="c.name || '-'" :value="c.name || ''" />
            </datalist>
          </div>
          <div>
            <label class="label">{{ t('catalog.price') }}</label>
            <input v-model="form.price" type="number" min="0" step="0.01" class="input" :readonly="!canEdit" />
          </div>
          <div>
            <label class="label">{{ t('catalog.currency') }}</label>
            <input v-model.trim="form.currency" class="input code uppercase" maxlength="8" placeholder="UAH" :readonly="!canEdit" />
          </div>
        </div>
        <div class="grid gap-4 md:grid-cols-2">
          <div>
            <label class="label">{{ t('catalog.url') }}</label>
            <input v-model.trim="form.url" type="url" class="input" maxlength="1000" :readonly="!canEdit" />
          </div>
          <div>
            <label class="label">{{ t('catalog.image_url') }}</label>
            <input v-model.trim="form.image_url" type="url" class="input" maxlength="1000" :readonly="!canEdit" />
          </div>
        </div>
        <div>
          <label class="label">{{ t('catalog.attributes') }}</label>
          <textarea v-model="form.attributes" rows="4" class="textarea code text-sm" :readonly="!canEdit" placeholder="color: red; black&#10;size: M" />
          <p class="hint">{{ t('catalog.attributes_hint') }}</p>
        </div>
        <div class="grid gap-4 md:grid-cols-3 items-end">
          <div>
            <label class="label">{{ t('catalog.language') }}</label>
            <select v-model="form.language" class="select" :disabled="!canEdit">
              <option v-for="l in languages" :key="l" :value="l">{{ l }}</option>
            </select>
          </div>
          <label class="flex items-center gap-2 text-sm pb-2">
            <input v-model="form.is_active" type="checkbox" class="checkbox" :disabled="!canEdit" />
            {{ t('catalog.active') }}
          </label>
        </div>
        <p v-if="editing?.index_error" class="alert-red text-sm">{{ indexError(editing.index_error) }}</p>
        <p v-if="formError" class="alert-red">{{ formError }}</p>
      </form>
      <template #footer>
        <button type="button" class="btn-secondary" @click="formOpen = false">{{ canEdit ? t('common.cancel') : t('common.close') }}</button>
        <button v-if="canEdit" type="submit" form="item-form" class="btn-primary" :disabled="busy">{{ t('common.save') }}</button>
      </template>
    </UiModal>

    <!-- import -->
    <UiModal :open="importOpen" :title="t('catalog.import')" size="lg" @close="importOpen = false">
      <form id="import-form" class="space-y-4" @submit.prevent="submitImport">
        <p class="text-sm muted whitespace-pre-line">{{ t('catalog.import_hint') }}</p>
        <pre class="pre text-xs">external_id,title,category,price,currency,description,attr:color
sku-1,Gel manicure,manicure,450,UAH,Gel polish and care,red|nude</pre>
        <div>
          <label class="label">{{ t('catalog.import_file') }}</label>
          <input type="file" accept=".csv,.json,text/csv,application/json" class="text-sm" @change="readFile" />
        </div>
        <div>
          <label class="label">{{ t('catalog.import_text') }}</label>
          <textarea v-model="importText" rows="8" class="textarea code text-xs" spellcheck="false" />
        </div>
        <label class="flex items-center gap-2 text-sm">
          <input v-model="deactivateMissing" type="checkbox" class="checkbox" />
          {{ t('catalog.deactivate_missing') }}
        </label>
        <p v-if="importResult" class="alert-green text-sm">{{ t('catalog.import_done', importResult) }}</p>
        <p v-if="importError" class="alert-red">{{ importError }}</p>
      </form>
      <template #footer>
        <button type="button" class="btn-secondary" @click="importOpen = false">{{ t('common.close') }}</button>
        <button type="submit" form="import-form" class="btn-primary" :disabled="busy || !importText.trim()">{{ t('catalog.import_run') }}</button>
      </template>
    </UiModal>
  </div>
</template>

<script setup lang="ts">
const { t, te } = useI18n()
const api = useApi()
const toast = useToast()
const confirm = useConfirm()
const { errorText } = useErrorText()
const { path, can } = useTenant()
const { run } = useAction()
const { num } = useFormat()
const { languages, load: loadInfo } = useTenantInfo()
usePage(() => [{ label: t('nav.catalog') }])

const LIMIT = 50
const STATUSES = ['indexed', 'pending', 'error', 'inactive']
const canEdit = computed(() => can('admin'))
const statusBox = ref<{ load: () => void } | null>(null)

const items = ref<any[]>([])
const total = ref(0)
const offset = ref(0)
const categories = ref<{ name: string | null; count: number }[]>([])
const filters = reactive({ q: '', category: '', status: '' })

const formOpen = ref(false)
const editing = ref<any>(null)
const busy = ref(false)
const formError = ref<string | null>(null)
const form = reactive({
  external_id: '',
  title: '',
  description: '',
  category: '',
  price: '' as string | number,
  currency: '',
  url: '',
  image_url: '',
  attributes: '',
  language: '',
  is_active: true,
})

const importOpen = ref(false)
const importText = ref('')
const deactivateMissing = ref(false)
const importResult = ref<Record<string, number> | null>(null)
const importError = ref<string | null>(null)

const { loading, error, load } = useLoader(async () => {
  const query: Record<string, any> = { limit: LIMIT, offset: offset.value }
  if (filters.q.trim()) query.q = filters.q.trim()
  if (filters.category) query.category = filters.category
  if (filters.status) query.status = filters.status
  const res = await api<{ items: any[]; total: number; categories: any[] }>(path('/catalog'), { query })
  items.value = res.items
  total.value = res.total
  categories.value = res.categories
  return res
})

function reload() {
  offset.value = 0
  load()
}

// index_error is a code (search.vectors_off) or a message from ES / Qdrant
function indexError(value: string) {
  const key = `errors.${value}`
  return te(key) ? t(key) : value
}

// attributes ⇄ "name: a; b" lines
function attrsToText(attrs: Record<string, any>) {
  return Object.entries(attrs || {})
    .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join('; ') : v}`)
    .join('\n')
}
function textToAttrs(text: string) {
  const out: Record<string, any> = {}
  for (const line of text.split('\n')) {
    const i = line.indexOf(':')
    if (i < 1) continue
    const key = line.slice(0, i).trim()
    const values = line.slice(i + 1).split(';').map((x) => x.trim()).filter(Boolean)
    if (!key || !values.length) continue
    out[key] = values.length > 1 ? values : values[0]
  }
  return out
}

function openForm(item: any | null) {
  editing.value = item
  Object.assign(form, {
    external_id: item?.external_id || '',
    title: item?.title || '',
    description: item?.description || '',
    category: item?.category || '',
    price: item?.price ?? '',
    currency: item?.currency || '',
    url: item?.url || '',
    image_url: item?.image_url || '',
    attributes: attrsToText(item?.attributes || {}),
    language: item?.language || languages.value[0] || 'en',
    is_active: item ? item.is_active : true,
  })
  formError.value = null
  formOpen.value = true
}

function body() {
  return {
    title: form.title.trim(),
    description: form.description.trim(),
    category: form.category.trim() || null,
    price: form.price === '' || form.price == null ? null : String(form.price),
    currency: form.currency.trim().toUpperCase() || null,
    url: form.url || null,
    image_url: form.image_url || null,
    attributes: textToAttrs(form.attributes),
    language: form.language || null,
    is_active: form.is_active,
  }
}

async function submit() {
  busy.value = true
  formError.value = null
  try {
    if (editing.value) {
      await api(path(`/catalog/${editing.value.id}`), { method: 'PATCH', body: body() })
    } else {
      await api(path('/catalog'), { method: 'POST', body: { external_id: form.external_id, ...body() } })
    }
    formOpen.value = false
    toast.ok(t('common.saved'))
    load()
    statusBox.value?.load()
  } catch (e) {
    formError.value = errorText(e)
  } finally {
    busy.value = false
  }
}

async function remove(item: any) {
  if (!(await confirm.confirm({ message: t('catalog.remove_confirm', { name: item.title }), tone: 'danger', confirmLabel: t('common.delete') }))) return
  const res = await run(() => api(path(`/catalog/${item.id}`), { method: 'DELETE' }), t('catalog.removed'))
  if (res) {
    load()
    statusBox.value?.load()
  }
}

function openImport() {
  importText.value = ''
  importResult.value = null
  importError.value = null
  deactivateMissing.value = false
  importOpen.value = true
}

async function readFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (file) importText.value = await file.text()
}

async function submitImport() {
  busy.value = true
  importError.value = null
  importResult.value = null
  try {
    const text = importText.value.trim()
    let payload: Record<string, any>
    if (text.startsWith('[') || text.startsWith('{')) {
      let data: any
      try {
        data = JSON.parse(text)
      } catch {
        importError.value = t('catalog.import_bad_json')
        return
      }
      payload = { items: Array.isArray(data) ? data : data.items || [] }
    } else {
      payload = { csv: text }
    }
    importResult.value = await api<Record<string, number>>(path('/catalog/import'), {
      method: 'POST',
      body: { ...payload, deactivate_missing: deactivateMissing.value },
    })
    load()
    statusBox.value?.load()
  } catch (e) {
    importError.value = errorText(e)
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  await loadInfo()
  load()
})
</script>
