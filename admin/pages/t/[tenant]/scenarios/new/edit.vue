<template>
  <ScenarioEditor :scenario-key="null" :draft="draft" />
</template>

<script setup lang="ts">
// a new scenario in the editor: a blank one, or the draft the start page prepared
// (a template, a description in words)
const draft = useScenarioDraft().take()
</script>
