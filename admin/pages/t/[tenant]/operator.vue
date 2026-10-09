<template>
  <div />
</template>

<script setup lang="ts">
// the old operator queue: the «waiting for you» tab of the Inbox replaced it
definePageMeta({
  middleware: (to) => navigateTo(`/t/${to.params.tenant}/inbox`, { replace: true }),
})
</script>
