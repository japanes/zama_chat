<template>
  <ScenarioEditor :key="scenarioKey" :scenario-key="scenarioKey" />
</template>

<script setup lang="ts">
// one saved scenario; a new one starts at scenarios/new
const route = useRoute()
const scenarioKey = computed(() => String(route.params.key))
</script>
