<template>
  <PromptEditor :prompt-key="key" :tenant-id="tenantId" :can-write="can('admin')" />
</template>

<script setup lang="ts">
const { t } = useI18n()
const route = useRoute()
const { tenantId, base, can } = useTenant()
const key = computed(() => String(route.params.key))
usePage(() => [{ label: t('nav.prompts'), to: `${base.value}/prompts` }, { label: key.value }])
</script>
