<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="page-title">{{ t('nav.endpoints') }}</h1>
        <p class="page-sub">{{ t('endpoints.sub') }}</p>
      </div>
      <button type="button" class="btn-primary" @click="openForm(null)">
        <UiIcon name="plus" :size="16" /> {{ t('endpoints.add') }}
      </button>
    </div>

    <div class="card overflow-hidden">
      <UiState :loading="loading" :error="error" :empty="!loading && !items.length ? t('endpoints.empty') : false" :has-data="items.length > 0" :retry="load">
        <div class="overflow-x-auto">
          <table class="table">
            <thead>
              <tr>
                <th>{{ t('endpoints.name') }}</th>
                <th>{{ t('endpoints.url') }}</th>
                <th>{{ t('endpoints.security') }}</th>
                <th>{{ t('endpoints.status') }}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              <tr v-for="e in items" :key="e.id">
                <td class="code font-medium">{{ e.name }}</td>
                <td class="text-sm break-all">{{ e.url }}</td>
                <td>
                  <span v-if="e.has_secret" class="badge-green">{{ t('endpoints.signed') }}</span>
                  <span v-for="h in e.header_names" :key="h" class="badge-gray code ml-1">{{ h }}</span>
                </td>
                <td>
                  <span :class="e.is_active ? 'badge-green' : 'badge-gray'">{{ e.is_active ? t('endpoints.active') : t('endpoints.off') }}</span>
                </td>
                <td class="text-right whitespace-nowrap">
                  <button type="button" class="btn-icon btn-ghost btn-sm" :aria-label="t('common.edit')" @click="openForm(e)">
                    <UiIcon name="edit" :size="16" />
                  </button>
                  <button type="button" class="btn-icon btn-ghost btn-sm text-rose-600" :aria-label="t('common.delete')" @click="remove(e)">
                    <UiIcon name="trash" :size="16" />
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </UiState>
    </div>

    <div class="card card-body text-sm space-y-2">
      <h2 class="card-title">{{ t('endpoints.how_title') }}</h2>
      <p class="muted">{{ t('endpoints.how_text') }}</p>
      <pre class="pre">X-Zama-Signature: sha256=HMAC_SHA256(secret, body)
Idempotency-Key: &lt;delivery id&gt;</pre>
    </div>

    <UiModal :open="formOpen" :title="editing ? t('endpoints.edit', { name: editing.name }) : t('endpoints.add')" size="lg" @close="formOpen = false">
      <form id="endpoint-form" class="space-y-4" @submit.prevent="submit">
        <div class="grid gap-4 md:grid-cols-2">
          <div>
            <label class="label">{{ t('endpoints.name') }}</label>
            <input v-model.trim="form.name" class="input code" required pattern="[a-z0-9_][a-z0-9_\-]{0,99}" :readonly="!!editing" />
            <p class="hint">{{ t('endpoints.name_hint') }}</p>
          </div>
          <div class="flex items-end">
            <label class="flex items-center gap-2 text-sm h-9">
              <input v-model="form.is_active" type="checkbox" class="checkbox" />
              {{ t('endpoints.active') }}
            </label>
          </div>
        </div>
        <div>
          <label class="label">{{ t('endpoints.url') }}</label>
          <input v-model.trim="form.url" type="url" class="input" required placeholder="https://" />
        </div>

        <div>
          <label class="label">{{ t('endpoints.secret') }}</label>
          <div v-if="editing" class="space-y-2">
            <label class="flex items-center gap-2 text-sm">
              <input v-model="form.rotate" type="checkbox" class="checkbox" />
              {{ t('endpoints.rotate_secret') }}
            </label>
            <input v-if="!form.rotate" v-model="form.secret" type="password" autocomplete="new-password" class="input" minlength="16" :placeholder="t('endpoints.secret_keep')" />
          </div>
          <input v-else v-model="form.secret" type="password" autocomplete="new-password" class="input" minlength="16" :placeholder="t('endpoints.secret_generate')" />
          <p class="hint">{{ t('endpoints.secret_hint') }}</p>
        </div>

        <div>
          <label class="label">{{ t('endpoints.headers') }}</label>
          <label v-if="editing" class="flex items-center gap-2 text-sm mb-2">
            <input v-model="form.replaceHeaders" type="checkbox" class="checkbox" />
            {{ t('endpoints.replace_headers', { names: editing.header_names.join(', ') || '—' }) }}
          </label>
          <template v-if="!editing || form.replaceHeaders">
            <div v-for="(h, i) in form.headers" :key="i" class="flex gap-2 mb-2">
              <input v-model.trim="h.name" class="input w-48 code" :placeholder="t('endpoints.header_name')" />
              <input v-model="h.value" type="password" autocomplete="off" class="input flex-1" :placeholder="t('endpoints.header_value')" />
              <button type="button" class="btn-icon btn-ghost text-rose-600" :aria-label="t('common.delete')" @click="form.headers.splice(i, 1)">
                <UiIcon name="trash" :size="16" />
              </button>
            </div>
            <button type="button" class="btn-ghost btn-sm" @click="form.headers.push({ name: '', value: '' })">
              <UiIcon name="plus" :size="14" /> {{ t('endpoints.add_header') }}
            </button>
          </template>
        </div>
        <p v-if="formError" class="alert-red">{{ formError }}</p>
      </form>
      <template #footer>
        <button type="button" class="btn-secondary" @click="formOpen = false">{{ t('common.cancel') }}</button>
        <button type="submit" form="endpoint-form" class="btn-primary" :disabled="busy">{{ t('common.save') }}</button>
      </template>
    </UiModal>

    <SecretOnce :value="newSecret" :title="t('endpoints.new_secret')" @close="newSecret = null" />
  </div>
</template>

<script setup lang="ts">
const { t } = useI18n()
const api = useApi()
const toast = useToast()
const confirm = useConfirm()
const { errorText } = useErrorText()
const { path } = useTenant()
const { run } = useAction()
usePage(() => [{ label: t('nav.endpoints') }])

const items = ref<any[]>([])
const formOpen = ref(false)
const editing = ref<any>(null)
const busy = ref(false)
const formError = ref<string | null>(null)
const newSecret = ref<string | null>(null)
const form = reactive({
  name: '',
  url: '',
  secret: '',
  rotate: false,
  is_active: true,
  replaceHeaders: false,
  headers: [] as { name: string; value: string }[],
})

const { loading, error, load } = useLoader(async () => {
  items.value = (await api<{ items: any[] }>(path('/endpoints'))).items
  return items.value
})

function openForm(e: any | null) {
  editing.value = e
  Object.assign(form, {
    name: e?.name || '',
    url: e?.url || '',
    secret: '',
    rotate: false,
    is_active: e ? e.is_active : true,
    replaceHeaders: false,
    headers: [],
  })
  formError.value = null
  formOpen.value = true
}

function headers() {
  const out: Record<string, string> = {}
  for (const h of form.headers) if (h.name) out[h.name] = h.value
  return out
}

async function submit() {
  busy.value = true
  formError.value = null
  try {
    let res: any
    if (editing.value) {
      const body: Record<string, any> = { url: form.url, is_active: form.is_active }
      if (form.rotate) body.rotate_secret = true
      else if (form.secret) body.secret = form.secret
      if (form.replaceHeaders) body.headers = headers()
      res = await api<any>(path(`/endpoints/${editing.value.id}`), { method: 'PATCH', body })
    } else {
      res = await api<any>(path('/endpoints'), {
        method: 'POST',
        body: { name: form.name, url: form.url, secret: form.secret || null, headers: headers(), is_active: form.is_active },
      })
    }
    formOpen.value = false
    if (res.secret) newSecret.value = res.secret
    toast.ok(t('common.saved'))
    load()
  } catch (e) {
    formError.value = errorText(e)
  } finally {
    busy.value = false
  }
}

async function remove(e: any) {
  if (!(await confirm.confirm({ message: t('endpoints.remove_confirm', { name: e.name }), tone: 'danger', confirmLabel: t('common.delete') }))) return
  const res = await run(() => api(path(`/endpoints/${e.id}`), { method: 'DELETE' }), t('endpoints.removed'))
  if (res) load()
}

onMounted(load)
</script>
