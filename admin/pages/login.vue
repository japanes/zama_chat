<template>
  <form :aria-label="t('auth.heading')" @submit.prevent="send">
    <h1 class="text-xl font-bold text-slate-900 dark:text-white">{{ t('auth.heading') }}</h1>
    <p class="page-sub">{{ t('auth.sub') }}</p>

    <p v-if="error" class="alert-red mt-4" role="alert">{{ error }}</p>

    <!-- link sent: "check your inbox" -->
    <div v-if="sent" class="mt-6 text-center">
      <span class="mx-auto grid place-items-center w-12 h-12 rounded-full bg-primary/10 text-primary">
        <UiIcon name="mail" :size="24" />
      </span>
      <p class="mt-4 text-sm text-slate-600 dark:text-slate-300">{{ t('auth.sent') }}</p>
      <p class="mt-1 font-medium break-all">{{ email }}</p>
      <!-- dev only (MAGIC_LINK_DEV_ECHO): no mail server, open the link from here -->
      <a v-if="devLink" :href="devLink" class="alert-amber mt-4 block text-left text-xs break-all">
        {{ t('auth.dev_link') }}: {{ devLink }}
      </a>
      <button type="button" class="btn-ghost mt-4" @click="reset">{{ t('auth.another_email') }}</button>
    </div>

    <template v-else>
      <!-- Google first: the main way in -->
      <a :href="googleUrl" class="btn-secondary w-full h-10 mt-6">
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" />
          <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
          <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" />
          <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
        </svg>
        {{ t('auth.google') }}
      </a>

      <div class="my-5 flex items-center gap-3 text-xs uppercase tracking-wider text-slate-400">
        <span class="flex-1 border-t border-slate-200 dark:border-slate-700" />
        {{ t('auth.or') }}
        <span class="flex-1 border-t border-slate-200 dark:border-slate-700" />
      </div>

      <label for="email" class="label">{{ t('auth.email') }}</label>
      <input
        id="email"
        v-model.trim="email"
        type="email"
        autocomplete="email"
        required
        :placeholder="t('auth.email_placeholder')"
        class="input h-10"
      />
      <p class="hint">{{ t('auth.magic_hint') }}</p>
      <button type="submit" class="btn-primary w-full h-10 mt-4" :disabled="loading">
        {{ loading ? t('common.sending') : t('auth.send_link') }}
      </button>
    </template>
  </form>
</template>

<script setup lang="ts">
definePageMeta({ layout: 'auth', public: true })

const { t, locale } = useI18n()
const route = useRoute()
const api = useApi()
const { errorText, codeText } = useErrorText()
useHead({ title: () => `${t('auth.heading')} · Zama Chat` })

const email = ref('')
const loading = ref(false)
const sent = ref(false)
const devLink = ref<string | null>(null)
// Google ends here with ?error=<code> when something went wrong
const error = ref<string | null>(
  typeof route.query.error === 'string' ? codeText({ code: route.query.error }) : null,
)

const next = computed(() => (typeof route.query.next === 'string' ? route.query.next : ''))
const googleUrl = computed(
  () => `/api/v1/admin/auth/google/start${next.value ? `?next=${encodeURIComponent(next.value)}` : ''}`,
)

async function send() {
  error.value = null
  loading.value = true
  try {
    const res = await api<{ ok: boolean; dev_link?: string }>('/auth/magic-link', {
      method: 'POST',
      body: { email: email.value, locale: locale.value },
      noAuthRedirect: true,
    })
    devLink.value = res.dev_link || null
    sent.value = true
  } catch (e) {
    error.value = errorText(e)
  } finally {
    loading.value = false
  }
}

function reset() {
  sent.value = false
  devLink.value = null
  error.value = null
}
</script>
