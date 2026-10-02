<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="page-title">{{ t('nav.users') }}</h1>
        <p class="page-sub">{{ t('users.sub') }}</p>
      </div>
      <button type="button" class="btn-primary" @click="openCreate">
        <UiIcon name="plus" :size="16" /> {{ t('users.add') }}
      </button>
    </div>

    <div class="card card-body">
      <input v-model.trim="q" type="search" class="input sm:max-w-sm" :placeholder="t('users.search')" />
    </div>

    <div class="card overflow-hidden">
      <UiState :loading="loading" :error="error" :empty="!loading && !items.length" :has-data="items.length > 0" :retry="load">
        <div class="overflow-x-auto">
          <table class="table">
            <thead>
              <tr>
                <th>{{ t('users.user') }}</th>
                <th>{{ t('users.access') }}</th>
                <th>{{ t('users.last_login') }}</th>
                <th>{{ t('users.status') }}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              <tr v-for="u in items" :key="u.id" :class="u.is_active ? '' : 'opacity-60'">
                <td>
                  <p class="font-medium">{{ u.name || u.email }}</p>
                  <p v-if="u.name" class="text-xs muted">{{ u.email }}</p>
                  <span v-if="u.has_google" class="badge-gray mt-1">Google</span>
                </td>
                <td>
                  <div class="flex flex-wrap gap-1">
                    <span v-if="u.is_superadmin" class="badge-red">{{ t('roles.superadmin') }}</span>
                    <NuxtLink v-for="m in u.memberships" :key="m.tenant_id" :to="`/t/${m.slug}/members`" class="badge-blue hover:underline">
                      {{ m.title }} · {{ t(`roles.${m.role}`) }}
                    </NuxtLink>
                    <span v-if="!u.is_superadmin && !u.memberships.length" class="text-xs muted">{{ t('users.no_access') }}</span>
                  </div>
                </td>
                <td class="text-sm whitespace-nowrap">{{ u.last_login_at ? f.relative(u.last_login_at) : t('users.never') }}</td>
                <td>
                  <span :class="u.is_active ? 'badge-green' : 'badge-gray'">{{ u.is_active ? t('users.active') : t('users.blocked') }}</span>
                </td>
                <td class="text-right">
                  <button type="button" class="btn-icon btn-ghost btn-sm" :aria-label="t('common.edit')" @click="openEdit(u)">
                    <UiIcon name="edit" :size="16" />
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <UiPagination v-model:offset="offset" :total="total" :limit="LIMIT" />
      </UiState>
    </div>

    <UiModal :open="createOpen" :title="t('users.add')" @close="createOpen = false">
      <form id="user-create" class="space-y-4" @submit.prevent="create">
        <div>
          <label class="label">{{ t('users.email') }}</label>
          <input v-model.trim="form.email" type="email" class="input" required />
        </div>
        <div>
          <label class="label">{{ t('users.name') }}</label>
          <input v-model.trim="form.name" class="input" maxlength="200" />
        </div>
        <label class="flex items-center gap-2 text-sm">
          <input v-model="form.is_superadmin" type="checkbox" class="checkbox" /> {{ t('roles.superadmin') }}
        </label>
        <div class="grid gap-3 sm:grid-cols-2">
          <div>
            <label class="label">{{ t('users.tenant') }}</label>
            <select v-model="form.tenant_id" class="select">
              <option value="">—</option>
              <option v-for="x in auth.tenants" :key="x.id" :value="x.id">{{ x.title }}</option>
            </select>
          </div>
          <div v-if="form.tenant_id">
            <label class="label">{{ t('members.role') }}</label>
            <select v-model="form.role" class="select">
              <option v-for="r in ['operator', 'admin', 'owner']" :key="r" :value="r">{{ t(`roles.${r}`) }}</option>
            </select>
          </div>
        </div>
        <p class="hint">{{ t('users.invite_hint') }}</p>
        <p v-if="formError" class="alert-red">{{ formError }}</p>
      </form>
      <template #footer>
        <button type="button" class="btn-secondary" @click="createOpen = false">{{ t('common.cancel') }}</button>
        <button type="submit" form="user-create" class="btn-primary" :disabled="busy">{{ t('common.create') }}</button>
      </template>
    </UiModal>

    <UiModal :open="!!editing" :title="editing?.email" @close="editing = null">
      <form v-if="editing" id="user-edit" class="space-y-4" @submit.prevent="save">
        <div>
          <label class="label">{{ t('users.name') }}</label>
          <input v-model.trim="editing.name" class="input" maxlength="200" />
        </div>
        <label class="flex items-center gap-2 text-sm">
          <input v-model="editing.is_superadmin" type="checkbox" class="checkbox" :disabled="editing.id === auth.user?.id" />
          {{ t('roles.superadmin') }}
        </label>
        <label class="flex items-center gap-2 text-sm">
          <input v-model="editing.is_active" type="checkbox" class="checkbox" :disabled="editing.id === auth.user?.id" />
          {{ t('users.active') }}
        </label>
        <p class="hint">{{ t('users.block_hint') }}</p>
        <p v-if="formError" class="alert-red">{{ formError }}</p>
      </form>
      <template #footer>
        <button type="button" class="btn-secondary" @click="editing = null">{{ t('common.cancel') }}</button>
        <button type="submit" form="user-edit" class="btn-primary" :disabled="busy">{{ t('common.save') }}</button>
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
const auth = useAuthStore()
const { errorText } = useErrorText()
usePage(() => [{ label: t('nav.system') }, { label: t('nav.users') }])

const LIMIT = 50
const items = ref<any[]>([])
const total = ref(0)
const offset = ref(0)
const q = ref('')
const createOpen = ref(false)
const editing = ref<any>(null)
const busy = ref(false)
const formError = ref<string | null>(null)
const form = reactive({ email: '', name: '', is_superadmin: false, tenant_id: '', role: 'admin' })

const { loading, error, load } = useLoader(async () => {
  const res = await api<{ items: any[]; total: number }>('/users', {
    query: { limit: LIMIT, offset: offset.value, ...(q.value ? { q: q.value } : {}) },
  })
  items.value = res.items
  total.value = res.total
  return res
})

watch(offset, load)
watchDebounced(
  q,
  () => {
    offset.value === 0 ? load() : (offset.value = 0)
  },
  { debounce: 300 },
)

function openCreate() {
  Object.assign(form, { email: '', name: '', is_superadmin: false, tenant_id: '', role: 'admin' })
  formError.value = null
  createOpen.value = true
}
async function create() {
  busy.value = true
  formError.value = null
  try {
    await api('/users', {
      method: 'POST',
      body: {
        email: form.email,
        name: form.name || null,
        is_superadmin: form.is_superadmin,
        tenant_id: form.tenant_id || null,
        role: form.tenant_id ? form.role : null,
      },
    })
    createOpen.value = false
    toast.ok(t('users.created'))
    load()
  } catch (e) {
    formError.value = errorText(e)
  } finally {
    busy.value = false
  }
}

function openEdit(u: any) {
  formError.value = null
  editing.value = { ...u }
}
async function save() {
  const u = editing.value
  busy.value = true
  formError.value = null
  try {
    const body: Record<string, any> = { name: u.name || null }
    if (u.id !== auth.user?.id) Object.assign(body, { is_superadmin: u.is_superadmin, is_active: u.is_active })
    await api(`/users/${u.id}`, { method: 'PATCH', body })
    editing.value = null
    toast.ok(t('common.saved'))
    load()
  } catch (e) {
    formError.value = errorText(e)
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>
