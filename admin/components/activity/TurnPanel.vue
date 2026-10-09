<template>
  <!-- one turn, loaded when opened: its trace, or (`plain`) the same in a person's words -->
  <div :class="plain ? '' : 'rounded-card border border-slate-200 dark:border-slate-700 bg-slate-50/60 dark:bg-slate-900/40 p-3'">
    <UiState :loading="loading" :error="error" :has-data="!!data" :retry="load">
      <template v-if="data">
        <TurnWhy v-if="plain" :detail="data" :runs="runs" inline />
        <TurnTrace v-else :detail="data" />
      </template>
    </UiState>
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{ turnId: string; preloaded?: any; plain?: boolean; runs?: any[] }>()
const api = useApi()
const { path } = useTenant()
const { data, loading, error, load } = useLoader(() => api<any>(path(`/turns/${props.turnId}`)))

onMounted(() => {
  if (props.preloaded) data.value = props.preloaded
  else load()
})
</script>
