<template>
  <div />
</template>

<script setup lang="ts">
// the old address of a conversation: it opens in the Inbox now
definePageMeta({
  middleware: (to) => navigateTo(`/t/${to.params.tenant}/inbox/${to.params.id}`, { replace: true }),
})
</script>
