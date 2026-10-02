<template>
  <div class="space-y-5">
    <div>
      <h1 class="page-title">{{ t('nav.shared_prompts') }}</h1>
      <p class="page-sub">{{ t('prompts.sub_shared') }}</p>
    </div>
    <PromptsList :tenant-id="null" link-base="/system/prompts" />
  </div>
</template>

<script setup lang="ts">
const { t } = useI18n()
usePage(() => [{ label: t('nav.shared_prompts') }])
</script>
