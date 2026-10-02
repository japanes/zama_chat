<template>
  <div class="space-y-5 max-w-2xl">
    <div>
      <h1 class="page-title">{{ t('nav.profile') }}</h1>
      <p class="page-sub">{{ auth.user?.email }}</p>
    </div>

    <form class="card card-body space-y-4" @submit.prevent="save">
      <div class="flex items-center gap-4">
        <img v-if="auth.user?.avatar_url" :src="auth.user.avatar_url" alt="" class="h-14 w-14 rounded-full object-cover" referrerpolicy="no-referrer" />
        <div v-else class="h-14 w-14 rounded-full bg-primary/15 text-primary flex items-center justify-center text-xl font-semibold">
          {{ (auth.user?.name || auth.user?.email || '?').slice(0, 1).toUpperCase() }}
        </div>
        <div class="min-w-0">
          <p class="font-medium truncate">{{ auth.user?.name || auth.user?.email }}</p>
          <p class="text-sm muted truncate">{{ auth.user?.email }}</p>
          <span v-if="auth.isSuper" class="badge-red mt-1">{{ t('roles.superadmin') }}</span>
        </div>
      </div>
      <div>
        <label class="label">{{ t('users.name') }}</label>
        <input v-model.trim="name" class="input" maxlength="200" />
      </div>
      <div>
        <label class="label">{{ t('profile.language') }}</label>
        <select v-model="lang" class="select">
          <option v-for="l in locales" :key="l.code" :value="l.code">{{ l.name }}</option>
        </select>
        <p class="hint">{{ t('profile.language_hint') }}</p>
      </div>
      <div class="flex justify-end">
        <button type="submit" class="btn-primary" :disabled="busy">{{ t('common.save') }}</button>
      </div>
    </form>

    <div class="card card-body">
      <h2 class="card-title mb-3">{{ t('profile.access') }}</h2>
      <ul v-if="auth.tenants.length" class="divide-y divide-slate-100 dark:divide-slate-700">
        <li v-for="x in auth.tenants" :key="x.id" class="flex items-center justify-between py-2">
          <NuxtLink :to="`/t/${x.slug}/results`" class="hover:underline">{{ x.title }}</NuxtLink>
          <span class="badge-blue">{{ t(`roles.${auth.roleIn(x.id)}`) }}</span>
        </li>
      </ul>
      <p v-else class="text-sm muted">{{ t('users.no_access') }}</p>
    </div>

    <div class="card card-body flex flex-wrap items-center justify-between gap-3">
      <p class="text-sm muted">{{ t('profile.sign_in_hint') }}</p>
      <button type="button" class="btn-secondary" @click="auth.logout()">
        <UiIcon name="logout" :size="16" /> {{ t('auth.logout') }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useAuthStore } from '~/stores/auth'

const { t, locale, locales, setLocale } = useI18n()
const api = useApi()
const toast = useToast()
const auth = useAuthStore()
const { errorText } = useErrorText()
usePage(() => [{ label: t('nav.profile') }])

const name = ref(auth.user?.name || '')
const lang = ref<string>(locale.value)
const busy = ref(false)

async function save() {
  busy.value = true
  try {
    await api('/auth/me', { method: 'PATCH', body: { name: name.value || null, locale: lang.value } })
    if (lang.value !== locale.value) await setLocale(lang.value as any)
    await auth.fetchMe()
    toast.ok(t('common.saved'))
  } catch (e) {
    toast.error(errorText(e))
  } finally {
    busy.value = false
  }
}
</script>
