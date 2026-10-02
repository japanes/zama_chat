<template>
  <div class="min-h-screen grid place-items-center bg-slate-50 dark:bg-slate-900 px-4">
    <div class="text-center">
      <p class="text-5xl font-heading font-bold text-primary">{{ error?.statusCode || 500 }}</p>
      <p class="mt-3 text-slate-600 dark:text-slate-300">{{ t('common.error_page') }}</p>
      <button type="button" class="btn-primary mt-6" @click="clearError({ redirect: '/' })">
        {{ t('common.go_home') }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{ error: { statusCode?: number } | null }>()
const { t } = useI18n()
</script>
