<template>
  <div v-if="total > limit" class="flex items-center justify-between gap-3 px-3 py-3 text-sm">
    <span class="muted">{{ t('common.shown', { from: offset + 1, to: Math.min(offset + limit, total), total }) }}</span>
    <div class="flex gap-1">
      <button type="button" class="btn-secondary btn-sm" :disabled="offset === 0" @click="emit('update:offset', Math.max(0, offset - limit))">
        <UiIcon name="chevron-left" :size="14" />
      </button>
      <button type="button" class="btn-secondary btn-sm" :disabled="offset + limit >= total" @click="emit('update:offset', offset + limit)">
        <UiIcon name="chevron-right" :size="14" />
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{ total: number; limit: number; offset: number }>()
const emit = defineEmits<{ 'update:offset': [value: number] }>()
const { t } = useI18n()
</script>
