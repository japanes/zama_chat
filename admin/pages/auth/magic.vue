<template>
  <div class="text-center">
    <template v-if="error">
      <span class="mx-auto grid place-items-center w-12 h-12 rounded-full bg-rose-100 dark:bg-rose-500/15 text-rose-600">
        <UiIcon name="alert" :size="24" />
      </span>
      <p class="mt-4 text-sm">{{ error }}</p>
      <NuxtLink to="/login" class="btn-primary mt-6">{{ t('auth.back_to_login') }}</NuxtLink>
    </template>
    <template v-else>
      <span class="mx-auto block w-8 h-8 rounded-full border-2 border-primary border-t-transparent animate-spin" />
      <p class="mt-4 text-sm muted">{{ t('auth.verifying') }}</p>
    </template>
  </div>
</template>

<script setup lang="ts">
import { useAuthStore } from '~/stores/auth'

definePageMeta({ layout: 'auth', public: true })

const { t } = useI18n()
const route = useRoute()
const api = useApi()
const auth = useAuthStore()
const { errorText, codeText } = useErrorText()
useHead({ title: () => `${t('auth.heading')} · Zama Chat` })

const error = ref<string | null>(null)

onMounted(async () => {
  const token = typeof route.query.token === 'string' ? route.query.token : ''
  if (!token) {
    error.value = codeText({ code: 'auth.bad_token' })
    return
  }
  try {
    await api('/auth/magic-link/verify', { method: 'POST', body: { token }, noAuthRedirect: true })
    await auth.fetchMe()
    if (auth.user?.locale) await applyUserLocale(auth.user.locale)
    await navigateTo('/', { replace: true })
  } catch (e) {
    error.value = errorText(e)
  }
})
</script>
