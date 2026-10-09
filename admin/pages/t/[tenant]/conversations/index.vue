<template>
  <div />
</template>

<script setup lang="ts">
// the old address of the conversations list: they live in the Inbox now
definePageMeta({
  middleware: (to) => navigateTo(`/t/${to.params.tenant}/inbox`, { replace: true }),
})
</script>
