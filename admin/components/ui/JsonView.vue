<template>
  <div>
    <button
      v-if="collapsible"
      type="button"
      class="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
      @click="open = !open"
    >
      <UiIcon :name="open ? 'chevron-down' : 'chevron-right'" :size="12" />
      {{ label || t('common.json') }}
    </button>
    <pre v-if="!collapsible || open" class="pre" :class="collapsible ? 'mt-1.5' : ''">{{ text }}</pre>
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{ value: unknown; collapsible?: boolean; label?: string; startOpen?: boolean }>()
const { t } = useI18n()
const open = ref(!!props.startOpen)
const text = computed(() =>
  typeof props.value === 'string' ? props.value : JSON.stringify(props.value, null, 2),
)
</script>
