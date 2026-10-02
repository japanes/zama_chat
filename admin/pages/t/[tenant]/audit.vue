<template>
  <div class="space-y-5">
    <div>
      <h1 class="page-title">{{ t('nav.audit') }}</h1>
      <p class="page-sub">{{ t('audit.sub') }}</p>
    </div>
    <AuditList :tenant-id="tenantId" />
  </div>
</template>

<script setup lang="ts">
const { t } = useI18n()
const { tenantId } = useTenant()
usePage(() => [{ label: t('nav.audit') }])
</script>
