<template>
  <button type="button" class="btn-sm btn-secondary" @click="copy">
    <UiIcon :name="copied ? 'check' : 'copy'" :size="14" />
    {{ copied ? t('common.copied') : t('common.copy') }}
  </button>
</template>

<script setup lang="ts">
const props = defineProps<{ value: string }>()
const { t } = useI18n()
const copied = ref(false)
async function copy() {
  try {
    await navigator.clipboard.writeText(props.value)
    copied.value = true
    setTimeout(() => (copied.value = false), 1500)
  } catch {
    // clipboard blocked: the value stays on screen to copy by hand
  }
}
</script>
