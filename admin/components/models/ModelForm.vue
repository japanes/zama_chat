<template>
  <!-- prices are USD per 1M tokens, as in the Gateway catalog -->
  <div class="space-y-4">
    <div class="grid gap-4 md:grid-cols-2">
      <div>
        <label class="label">{{ t('models.name') }}</label>
        <input v-model.trim="model.name" class="input code" required :readonly="nameLocked" placeholder="openai/…" />
      </div>
      <div>
        <label class="label">{{ t('models.label') }}</label>
        <input v-model.trim="model.label" class="input" />
      </div>
      <div>
        <label class="label">{{ t('models.kind') }}</label>
        <select v-model="model.kind" class="select">
          <option value="language">{{ t('models.kinds.language') }}</option>
          <option value="embedding">{{ t('models.kinds.embedding') }}</option>
        </select>
      </div>
      <div class="flex items-end">
        <label class="flex items-center gap-2 text-sm h-9">
          <input v-model="model.is_active" type="checkbox" class="checkbox" />
          {{ t('models.active') }}
        </label>
      </div>
    </div>

    <div>
      <p class="label">{{ t('models.prices') }}</p>
      <div class="grid gap-3 grid-cols-1 sm:grid-cols-3">
        <div>
          <label class="text-xs muted">{{ t('models.price_input') }}</label>
          <input v-model="model.price_input" type="number" min="0" step="any" class="input" required />
        </div>
        <div>
          <label class="text-xs muted">{{ t('models.price_cached_input') }}</label>
          <input v-model="model.price_cached_input" type="number" min="0" step="any" class="input" />
        </div>
        <div>
          <label class="text-xs muted">{{ t('models.price_output') }}</label>
          <input v-model="model.price_output" type="number" min="0" step="any" class="input" required />
        </div>
      </div>
      <p class="hint">{{ t('models.prices_hint') }}</p>
    </div>

    <div class="grid gap-4 sm:grid-cols-2">
      <div>
        <label class="label">{{ t('models.context_window') }}</label>
        <input v-model.number="model.context_window" type="number" min="1" class="input" />
      </div>
      <div>
        <label class="label">{{ t('models.max_output') }}</label>
        <input v-model.number="model.max_output" type="number" min="1" class="input" />
      </div>
    </div>

    <div class="flex flex-wrap gap-x-5 gap-y-2">
      <label class="flex items-center gap-2 text-sm">
        <input v-model="model.supports_json_schema" type="checkbox" class="checkbox" /> {{ t('models.json_schema') }}
      </label>
      <label class="flex items-center gap-2 text-sm">
        <input v-model="model.supports_reasoning" type="checkbox" class="checkbox" /> {{ t('models.reasoning') }}
      </label>
      <label class="flex items-center gap-2 text-sm">
        <input v-model="model.supports_vision" type="checkbox" class="checkbox" /> {{ t('models.vision') }}
      </label>
    </div>
    <div v-if="model.supports_reasoning">
      <label class="label">{{ t('models.reasoning_efforts') }}</label>
      <input v-model="effortsText" class="input code" placeholder="low, medium, high" />
      <p class="hint">{{ t('models.reasoning_efforts_hint') }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{ model: any; nameLocked?: boolean }>()
const { t } = useI18n()

const effortsText = computed({
  get: () => (props.model.reasoning_efforts || []).join(', '),
  set: (value: string) => {
    props.model.reasoning_efforts = value
      .split(',')
      .map((x) => x.trim())
      .filter(Boolean)
  },
})
</script>
