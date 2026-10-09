<template>
  <div v-if="tenant" class="h-full">
    <NuxtPage />
  </div>
  <div v-else class="max-w-lg mx-auto mt-16 text-center">
    <h1 class="page-title">{{ t('common.tenant_missing') }}</h1>
    <NuxtLink to="/" class="btn-primary mt-6">{{ t('common.go_home') }}</NuxtLink>
  </div>
</template>

<script setup lang="ts">
// /t/<slug>/…: the tenant must be one of the user's (a superadmin has all of them)
const { t } = useI18n()
const { tenant } = useTenant()
</script>
