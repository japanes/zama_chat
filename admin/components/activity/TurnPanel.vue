<template>
  <!-- the trace of one turn, loaded when opened -->
  <div class="rounded-card border border-slate-200 dark:border-slate-700 bg-slate-50/60 dark:bg-slate-900/40 p-3">
    <UiState :loading="loading" :error="error" :has-data="!!data" :retry="load">
      <TurnTrace v-if="data" :detail="data" />
    </UiState>
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{ turnId: string; preloaded?: any }>()
const api = useApi()
const { path } = useTenant()
const { data, loading, error, load } = useLoader(() => api<any>(path(`/turns/${props.turnId}`)))

onMounted(() => {
  if (props.preloaded) data.value = props.preloaded
  else load()
})
</script>
