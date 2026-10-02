<template>
  <div class="space-y-5">
    <div>
      <h1 class="page-title">{{ t('nav.prompts') }}</h1>
      <p class="page-sub">{{ t('prompts.sub_tenant') }}</p>
    </div>
    <PromptsList :tenant-id="tenantId" :link-base="`${base}/prompts`" />
  </div>
</template>

<script setup lang="ts">
const { t } = useI18n()
const { tenantId, base } = useTenant()
usePage(() => [{ label: t('nav.prompts') }])
</script>
