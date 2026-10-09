<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="page-title">{{ t('nav.tenants') }}</h1>
        <p class="page-sub">{{ t('tenants.sub') }}</p>
      </div>
      <button type="button" class="btn-primary" @click="openForm">
        <UiIcon name="plus" :size="16" /> {{ t('tenants.add') }}
      </button>
    </div>

    <div class="card overflow-hidden">
      <UiState :loading="loading" :error="error" :empty="!loading && !items.length" :has-data="items.length > 0" :retry="load">
        <div class="overflow-x-auto">
          <table class="table">
            <thead>
              <tr>
                <th>{{ t('tenants.title') }}</th>
                <th>{{ t('tenants.slug') }}</th>
                <th>{{ t('settings.languages') }}</th>
                <th>{{ t('tenants.created') }}</th>
                <th>{{ t('tenants.active') }}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              <tr v-for="x in items" :key="x.id" :class="x.is_active ? '' : 'opacity-60'">
                <td class="font-medium">{{ x.title }}</td>
                <td class="code text-sm">{{ x.slug }}</td>
                <td class="text-sm">{{ (x.settings.languages || []).join(', ') || '—' }}</td>
                <td class="text-sm whitespace-nowrap">{{ f.date(x.created_at) }}</td>
                <td>
                  <label class="switch" :title="x.is_active ? t('tenants.deactivate') : t('tenants.activate')">
                    <input type="checkbox" :checked="x.is_active" :disabled="busy" @change="toggle(x)" />
                    <span />
                  </label>
                </td>
                <td class="text-right whitespace-nowrap">
                  <NuxtLink :to="`/t/${x.slug}/results`" class="btn-ghost btn-sm">{{ t('tenants.open') }}</NuxtLink>
                  <NuxtLink :to="`/t/${x.slug}/settings/bot`" class="btn-icon btn-ghost btn-sm" :aria-label="t('nav.settings')">
                    <UiIcon name="settings" :size="16" />
                  </NuxtLink>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </UiState>
    </div>

    <UiModal :open="formOpen" :title="t('tenants.add')" @close="formOpen = false">
      <form id="tenant-form" class="space-y-4" @submit.prevent="create">
        <div>
          <label class="label">{{ t('tenants.title') }}</label>
          <input v-model.trim="form.title" class="input" required maxlength="200" @input="autoSlug" />
        </div>
        <div>
          <label class="label">{{ t('tenants.slug') }}</label>
          <input v-model.trim="form.slug" class="input code" required minlength="3" maxlength="64" pattern="[a-z0-9][a-z0-9\-]*" @input="slugTouched = true" />
          <p class="hint">{{ t('tenants.slug_hint') }}</p>
        </div>
        <div>
          <label class="label">{{ t('settings.languages') }}</label>
          <input v-model="form.languages" class="input code" placeholder="uk, en" />
          <p class="hint">{{ t('tenants.languages_hint') }}</p>
        </div>
        <p v-if="formError" class="alert-red">{{ formError }}</p>
      </form>
      <template #footer>
        <button type="button" class="btn-secondary" @click="formOpen = false">{{ t('common.cancel') }}</button>
        <button type="submit" form="tenant-form" class="btn-primary" :disabled="busy">{{ t('common.create') }}</button>
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
usePage(() => [{ label: t('nav.system') }, { label: t('nav.tenants') }])

const items = ref<any[]>([])
const formOpen = ref(false)
const busy = ref(false)
const formError = ref<string | null>(null)
const slugTouched = ref(false)
const form = reactive({ title: '', slug: '', languages: 'uk, en' })

const { loading, error, load } = useLoader(async () => {
  items.value = (await api<{ items: any[] }>('/tenants')).items
  return items.value
})

function openForm() {
  Object.assign(form, { title: '', slug: '', languages: 'uk, en' })
  slugTouched.value = false
  formError.value = null
  formOpen.value = true
}
function autoSlug() {
  if (slugTouched.value) return
  form.slug = form.title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64)
}

async function create() {
  busy.value = true
  formError.value = null
  try {
    const languages = form.languages
      .split(',')
      .map((x) => x.trim().toLowerCase())
      .filter(Boolean)
    const res = await api<{ tenant: any }>('/tenants', {
      method: 'POST',
      body: { slug: form.slug, title: form.title, settings: languages.length ? { languages } : {} },
    })
    formOpen.value = false
    toast.ok(t('tenants.created_ok'))
    await auth.fetchMe()
    await navigateTo(`/t/${res.tenant.slug}/settings/bot`)
  } catch (e) {
    formError.value = errorText(e)
  } finally {
    busy.value = false
  }
}

async function toggle(x: any) {
  if (x.is_active && !(await confirm.confirm({ message: t('tenants.deactivate_confirm', { title: x.title }), tone: 'danger', confirmLabel: t('tenants.deactivate') }))) {
    load()
    return
  }
  busy.value = true
  try {
    await api(`/tenants/${x.id}`, { method: 'PATCH', body: { is_active: !x.is_active } })
    await Promise.all([load(), auth.fetchMe()])
  } catch (e) {
    toast.error(errorText(e))
    load()
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>
