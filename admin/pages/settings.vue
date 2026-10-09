<template>
  <SettingsHub />
</template>

<script setup lang="ts">
// «Settings» without a tenant in the address: with a tenant go to its own hub
const { base } = useTenant()
if (base.value) await navigateTo(`${base.value}/settings`, { replace: true })
</script>
