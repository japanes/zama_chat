<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="page-title">{{ t('nav.knowledge') }}</h1>
        <p class="page-sub">{{ t('knowledge.sub') }}</p>
      </div>
      <button v-if="canEdit" type="button" class="btn-primary" @click="openForm(null)">
        <UiIcon name="plus" :size="16" /> {{ t('knowledge.add') }}
      </button>
    </div>

    <SearchStatus ref="statusBox" kind="knowledge" :can-edit="canEdit" @reindexed="load" />

    <div class="card overflow-hidden">
      <div class="p-3 border-b border-slate-200 dark:border-slate-700">
        <input v-model="q" class="input md:w-64" :placeholder="t('knowledge.search_placeholder')" @keyup.enter="reload" />
      </div>
      <UiState :loading="loading" :error="error" :empty="!loading && !items.length ? t('knowledge.empty') : false" :has-data="items.length > 0" :retry="load">
        <div class="overflow-x-auto">
          <table class="table table-hover">
            <thead>
              <tr>
                <th>{{ t('knowledge.title') }}</th>
                <th>{{ t('knowledge.size') }}</th>
                <th>{{ t('knowledge.chunks') }}</th>
                <th>{{ t('catalog.index') }}</th>
                <th>{{ t('knowledge.updated') }}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              <tr v-for="s in items" :key="s.id">
                <td>
                  <div class="font-medium">{{ s.title }}</div>
                  <div class="text-xs muted"><span class="uppercase">{{ s.language }}</span><span v-if="s.url"> · {{ s.url }}</span></div>
                </td>
                <td class="text-sm">{{ num(s.size) }}</td>
                <td class="text-sm">{{ num(s.chunks_count) }}</td>
                <td>
                  <span v-if="!s.is_active" class="badge-gray">{{ t('status.index.inactive') }}</span>
                  <StatusBadge v-else kind="index" :value="s.index_status" />
                  <div v-if="s.index_error" class="text-xs text-rose-600 mt-1">{{ indexError(s.index_error) }}</div>
                </td>
                <td class="text-sm muted whitespace-nowrap">{{ relative(s.updated_at) }}</td>
                <td class="text-right whitespace-nowrap">
                  <button type="button" class="btn-icon btn-ghost btn-sm" :aria-label="canEdit ? t('common.edit') : t('common.open')" @click="openForm(s)">
                    <UiIcon :name="canEdit ? 'edit' : 'external'" :size="16" />
                  </button>
                  <button v-if="canEdit" type="button" class="btn-icon btn-ghost btn-sm text-rose-600" :aria-label="t('common.delete')" @click="remove(s)">
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

    <SearchTest default-kind="knowledge" />

    <UiModal :open="formOpen" :title="editing ? editing.title : t('knowledge.add')" size="xl" @close="formOpen = false">
      <UiState :loading="formLoading" :has-data="!formLoading">
        <form id="source-form" class="space-y-4" @submit.prevent="submit">
          <div class="grid gap-4 md:grid-cols-[1fr_8rem]">
            <div>
              <label class="label">{{ t('knowledge.title') }}</label>
              <input v-model="form.title" class="input" required maxlength="300" :readonly="!canEdit" />
            </div>
            <div>
              <label class="label">{{ t('catalog.language') }}</label>
              <select v-model="form.language" class="select" :disabled="!canEdit">
                <option v-for="l in languages" :key="l" :value="l">{{ l }}</option>
              </select>
            </div>
          </div>
          <div>
            <label class="label">{{ t('knowledge.url') }} <span class="muted font-normal">({{ t('common.optional') }})</span></label>
            <input v-model.trim="form.url" type="url" class="input" maxlength="1000" :readonly="!canEdit" />
            <p class="hint">{{ t('knowledge.url_hint') }}</p>
          </div>
          <div>
            <label class="label">{{ t('knowledge.body') }}</label>
            <textarea v-model="form.body" rows="16" class="textarea text-sm" required maxlength="200000" :readonly="!canEdit" />
            <p class="hint">{{ t('knowledge.body_hint', { n: num(form.body.length) }) }}</p>
          </div>
          <label class="flex items-center gap-2 text-sm">
            <input v-model="form.is_active" type="checkbox" class="checkbox" :disabled="!canEdit" />
            {{ t('catalog.active') }}
          </label>
          <p v-if="editing?.index_error" class="alert-red text-sm">{{ indexError(editing.index_error) }}</p>
          <p v-if="formError" class="alert-red">{{ formError }}</p>
        </form>
      </UiState>
      <template #footer>
        <button type="button" class="btn-secondary" @click="formOpen = false">{{ canEdit ? t('common.cancel') : t('common.close') }}</button>
        <button v-if="canEdit" type="submit" form="source-form" class="btn-primary" :disabled="busy || formLoading">{{ t('common.save') }}</button>
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
const { num, relative } = useFormat()
const { languages, load: loadInfo } = useTenantInfo()
usePage(() => [{ label: t('nav.knowledge') }])

const LIMIT = 50
const canEdit = computed(() => can('admin'))
const statusBox = ref<{ load: () => void } | null>(null)

const items = ref<any[]>([])
const total = ref(0)
const offset = ref(0)
const q = ref('')

const formOpen = ref(false)
const formLoading = ref(false)
const editing = ref<any>(null)
const busy = ref(false)
const formError = ref<string | null>(null)
const form = reactive({ title: '', body: '', url: '', language: '', is_active: true })

const { loading, error, load } = useLoader(async () => {
  const query: Record<string, any> = { limit: LIMIT, offset: offset.value }
  if (q.value.trim()) query.q = q.value.trim()
  const res = await api<{ items: any[]; total: number }>(path('/knowledge'), { query })
  items.value = res.items
  total.value = res.total
  return res
})

function reload() {
  offset.value = 0
  load()
}

function indexError(value: string) {
  const key = `errors.${value}`
  return te(key) ? t(key) : value
}

async function openForm(s: any | null) {
  editing.value = s
  Object.assign(form, { title: '', body: '', url: '', language: languages.value[0] || 'en', is_active: true })
  formError.value = null
  formOpen.value = true
  if (!s) return
  // the list has no body: load the source
  formLoading.value = true
  try {
    const res = await api<{ source: any }>(path(`/knowledge/${s.id}`))
    editing.value = res.source
    Object.assign(form, {
      title: res.source.title,
      body: res.source.body,
      url: res.source.url || '',
      language: res.source.language,
      is_active: res.source.is_active,
    })
  } catch (e) {
    formError.value = errorText(e)
  } finally {
    formLoading.value = false
  }
}

async function submit() {
  busy.value = true
  formError.value = null
  const body = {
    title: form.title.trim(),
    body: form.body.trim(),
    url: form.url || null,
    language: form.language || null,
    is_active: form.is_active,
  }
  try {
    if (editing.value) await api(path(`/knowledge/${editing.value.id}`), { method: 'PATCH', body })
    else await api(path('/knowledge'), { method: 'POST', body })
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

async function remove(s: any) {
  if (!(await confirm.confirm({ message: t('knowledge.remove_confirm', { name: s.title }), tone: 'danger', confirmLabel: t('common.delete') }))) return
  const res = await run(() => api(path(`/knowledge/${s.id}`), { method: 'DELETE' }), t('knowledge.removed'))
  if (res) {
    load()
    statusBox.value?.load()
  }
}

onMounted(async () => {
  await loadInfo()
  load()
})
</script>
