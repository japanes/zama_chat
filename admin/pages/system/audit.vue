<template>
  <div class="space-y-5">
    <div>
      <h1 class="page-title">{{ t('nav.system_audit') }}</h1>
      <p class="page-sub">{{ t('audit.sub_system') }}</p>
    </div>
    <AuditList :tenant-id="null" />
  </div>
</template>

<script setup lang="ts">
const { t } = useI18n()
usePage(() => [{ label: t('nav.system_audit') }])
</script>
