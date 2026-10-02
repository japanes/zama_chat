<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="page-title">{{ t('nav.members') }}</h1>
        <p class="page-sub">{{ t('members.sub') }}</p>
      </div>
      <button v-if="isOwner" type="button" class="btn-primary" @click="openAdd">
        <UiIcon name="plus" :size="16" /> {{ t('members.add') }}
      </button>
    </div>

    <div class="card overflow-hidden">
      <UiState :loading="loading" :error="error" :empty="!loading && !items.length" :has-data="items.length > 0" :retry="load">
        <div class="overflow-x-auto">
          <table class="table">
            <thead>
              <tr>
                <th>{{ t('members.user') }}</th>
                <th>{{ t('members.role') }}</th>
                <th>{{ t('members.last_login') }}</th>
                <th v-if="isOwner" />
              </tr>
            </thead>
            <tbody>
              <tr v-for="m in items" :key="m.user_id">
                <td>
                  <div class="flex items-center gap-2.5">
                    <img v-if="m.avatar_url" :src="m.avatar_url" alt="" class="w-8 h-8 rounded-full" referrerpolicy="no-referrer" />
                    <span v-else class="grid place-items-center w-8 h-8 rounded-full bg-slate-100 dark:bg-slate-700 text-xs font-semibold">{{ (m.name || m.email).slice(0, 2).toUpperCase() }}</span>
                    <div class="min-w-0">
                      <p class="font-medium truncate">{{ m.name || m.email }}</p>
                      <p v-if="m.name" class="text-xs muted truncate">{{ m.email }}</p>
                    </div>
                    <span v-if="!m.is_active" class="badge-red">{{ t('members.inactive') }}</span>
                  </div>
                </td>
                <td>
                  <select v-if="isOwner" :value="m.role" class="select h-8 w-36" @change="changeRole(m, ($event.target as HTMLSelectElement).value)">
                    <option v-for="r in ROLES" :key="r" :value="r">{{ t(`roles.${r}`) }}</option>
                  </select>
                  <span v-else class="badge-gray">{{ t(`roles.${m.role}`) }}</span>
                </td>
                <td class="text-sm muted whitespace-nowrap">{{ f.dateTime(m.last_login_at) }}</td>
                <td v-if="isOwner" class="text-right">
                  <button type="button" class="btn-icon btn-ghost btn-sm text-rose-600" :aria-label="t('common.delete')" @click="remove(m)">
                    <UiIcon name="trash" :size="16" />
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </UiState>
    </div>

    <div class="card card-body text-sm space-y-1">
      <p v-for="r in ROLES" :key="r"><span class="font-medium">{{ t(`roles.${r}`) }}</span> — <span class="muted">{{ t(`members.role_hint.${r}`) }}</span></p>
    </div>

    <UiModal :open="addOpen" :title="t('members.add')" @close="addOpen = false">
      <form id="member-form" class="space-y-4" @submit.prevent="add">
        <div>
          <label class="label">{{ t('auth.email') }}</label>
          <input v-model.trim="form.email" type="email" required class="input" />
        </div>
        <div>
          <label class="label">{{ t('members.name') }} <span class="muted font-normal">({{ t('common.optional') }})</span></label>
          <input v-model.trim="form.name" class="input" />
        </div>
        <div>
          <label class="label">{{ t('members.role') }}</label>
          <select v-model="form.role" class="select">
            <option v-for="r in ROLES" :key="r" :value="r">{{ t(`roles.${r}`) }}</option>
          </select>
        </div>
        <p class="hint">{{ t('members.invite_hint') }}</p>
      </form>
      <template #footer>
        <button type="button" class="btn-secondary" @click="addOpen = false">{{ t('common.cancel') }}</button>
        <button type="submit" form="member-form" class="btn-primary" :disabled="busy">{{ t('common.add') }}</button>
      </template>
    </UiModal>
  </div>
</template>

<script setup lang="ts">
const ROLES = ['operator', 'admin', 'owner'] as const

const { t } = useI18n()
const api = useApi()
const f = useFormat()
const confirm = useConfirm()
const { path, can } = useTenant()
const { busy, run } = useAction()
usePage(() => [{ label: t('nav.members') }])

const isOwner = computed(() => can('owner'))
const items = ref<any[]>([])
const addOpen = ref(false)
const form = reactive({ email: '', name: '', role: 'operator' })

const { loading, error, load } = useLoader(async () => {
  items.value = (await api<{ items: any[] }>(path('/members'))).items
  return items.value
})

function openAdd() {
  Object.assign(form, { email: '', name: '', role: 'operator' })
  addOpen.value = true
}
async function add() {
  const res = await run(
    () => api<any>(path('/members'), { method: 'POST', body: { email: form.email, role: form.role, name: form.name || null } }),
    t('members.added'),
  )
  if (res) {
    addOpen.value = false
    load()
  }
}
async function changeRole(m: any, role: string) {
  const res = await run(() => api(path(`/members/${m.user_id}`), { method: 'PATCH', body: { role } }), t('common.saved'))
  if (!res) load() // put the old value back
  else m.role = role
}
async function remove(m: any) {
  if (!(await confirm.confirm({ message: t('members.remove_confirm', { email: m.email }), tone: 'danger', confirmLabel: t('common.delete') }))) return
  const res = await run(() => api(path(`/members/${m.user_id}`), { method: 'DELETE' }), t('members.removed'))
  if (res) load()
}

onMounted(load)
</script>
