<template>
  <!-- a key / secret the server shows only once -->
  <UiModal :open="!!value" :title="title" @close="emit('close')">
    <p class="alert-amber">{{ t('common.secret_once') }}</p>
    <div class="mt-4 flex items-center gap-2">
      <code class="flex-1 min-w-0 pre max-h-none break-all">{{ value }}</code>
    </div>
    <template #footer>
      <CopyButton :value="value || ''" />
      <button type="button" class="btn-primary" @click="emit('close')">{{ t('common.done') }}</button>
    </template>
  </UiModal>
</template>

<script setup lang="ts">
defineProps<{ value: string | null; title: string }>()
const emit = defineEmits<{ close: [] }>()
const { t } = useI18n()
</script>
