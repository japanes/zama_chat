<template>
  <div class="flex items-baseline justify-between gap-3">
    <span class="muted shrink-0">{{ label }}</span>
    <span class="text-right min-w-0"><slot /></span>
  </div>
</template>

<script setup lang="ts">
defineProps<{ label: string }>()
</script>
