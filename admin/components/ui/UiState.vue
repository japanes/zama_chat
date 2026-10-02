<template>
  <!-- loading / error / empty placeholder for a list or a page -->
  <div v-if="loading && !hasData" class="py-12 grid place-items-center">
    <span class="w-7 h-7 rounded-full border-2 border-primary border-t-transparent animate-spin" />
  </div>
  <div v-else-if="error" class="alert-red flex items-center justify-between gap-3">
    <span>{{ error }}</span>
    <button v-if="retry" type="button" class="btn-sm btn-secondary" @click="retry">
      <UiIcon name="refresh" :size="14" /> {{ t('common.retry') }}
    </button>
  </div>
  <div v-else-if="empty" class="py-12 text-center text-sm muted">
    {{ empty === true ? t('common.empty') : empty }}
  </div>
  <slot v-else />
</template>

<script setup lang="ts">
defineProps<{
  loading?: boolean
  error?: string | null
  empty?: boolean | string
  hasData?: boolean
  retry?: () => void
}>()
const { t } = useI18n()
</script>
