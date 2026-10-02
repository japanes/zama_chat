<template>
  <!-- a tiny real call through the Gateway; costs a fraction of a cent -->
  <div class="space-y-2">
    <div class="flex flex-wrap items-center gap-2">
      <button type="button" class="btn-secondary btn-sm" :disabled="busy || !name" @click="check">
        <UiIcon name="check" :size="14" /> {{ busy ? t('models.checking') : t('models.check') }}
      </button>
      <select v-if="efforts.length" v-model="effort" class="select h-8 w-auto">
        <option value="">{{ t('models.effort_off') }}</option>
        <option v-for="e in efforts" :key="e" :value="e">{{ e }}</option>
      </select>
    </div>
    <div v-if="result" :class="result.ok ? 'alert-green' : 'alert-red'" class="text-sm">
      <template v-if="result.ok">
        {{ t('models.check_ok', { ms: result.latency_ms }) }}
        <span v-if="result.answer" class="code">«{{ result.answer }}»</span>
        <span v-if="result.dimensions">· {{ t('models.dimensions', { n: result.dimensions }) }}</span>
        <span v-if="result.cost">· {{ f.money(result.cost) }}</span>
      </template>
      <template v-else>{{ result.error ? codeText(result.error) : t('models.check_failed') }}</template>
    </div>
  </div>
</template>

<script setup lang="ts">
const props = withDefaults(defineProps<{ name: string; kind: string; efforts?: string[] }>(), {
  efforts: () => [],
})
const { t } = useI18n()
const api = useApi()
const f = useFormat()
const toast = useToast()
const { errorText, codeText } = useErrorText()

const busy = ref(false)
const result = ref<any>(null)
const effort = ref('')
watch(() => props.name, () => (result.value = null))

async function check() {
  busy.value = true
  result.value = null
  try {
    result.value = await api<any>('/models/check', {
      method: 'POST',
      body: { name: props.name, kind: props.kind, reasoning_effort: effort.value || null },
    })
  } catch (e) {
    toast.error(errorText(e))
  } finally {
    busy.value = false
  }
}
</script>
