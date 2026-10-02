<template>
  <div class="max-w-lg mx-auto mt-16 text-center">
    <span class="mx-auto grid place-items-center w-12 h-12 rounded-full bg-amber-100 dark:bg-amber-500/15 text-amber-600">
      <UiIcon name="alert" :size="24" />
    </span>
    <h1 class="page-title mt-4">{{ t('home.no_tenants') }}</h1>
    <p class="page-sub">{{ auth.isSuper ? t('home.no_tenants_super') : t('home.no_tenants_hint') }}</p>
    <NuxtLink v-if="auth.isSuper" to="/system/tenants" class="btn-primary mt-6">{{ t('nav.tenants') }}</NuxtLink>
  </div>
</template>

<script setup lang="ts">
import { useAuthStore } from '~/stores/auth'

// "/" opens the last tenant (or the first one); without tenants there is nothing to show
const { t } = useI18n()
const auth = useAuthStore()
usePage(() => [{ label: t('home.title') }])

const last = lastTenantSlug()
const target = auth.tenants.find((x) => x.slug === last) || auth.tenants[0]
if (target) await navigateTo(`/t/${target.slug}/results`, { replace: true })
</script>
