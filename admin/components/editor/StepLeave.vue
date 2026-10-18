<template>
  <div class="space-y-5">
    <!-- step 5: the client changed their mind, came back after a pause, or another
         scenario is already going -->
    <div>
      <label class="label" :for="`${uid}-cancel`">{{ t('editor.leave.cancel') }}</label>
      <LocalizedInput
        :model-value="def.cancel.description"
        :input-id="`${uid}-cancel`"
        :languages="languages"
        :main="def.language"
        multiline
        optional
        :readonly="readonly"
        :placeholder="t('editor.leave.cancel_placeholder')"
        @update:model-value="def.cancel.description = $event"
      />
      <p class="hint">{{ t('editor.leave.cancel_hint') }}</p>
    </div>

    <fieldset class="space-y-1">
      <legend class="label">{{ t('editor.leave.resume') }}</legend>
      <label class="flex items-center gap-2.5 min-h-[44px]">
        <input v-model="def.resume.mode" type="radio" class="accent-primary-dark" value="always" :name="`${uid}-resume`" :disabled="readonly" />
        {{ t('editor.leave.resume_always') }}
      </label>
      <label class="flex flex-wrap items-center gap-2.5 min-h-[44px]">
        <input v-model="def.resume.mode" type="radio" class="accent-primary-dark" value="within_hours" :name="`${uid}-resume`" :disabled="readonly" />
        <span>{{ t('editor.leave.resume_within') }}</span>
        <input
          v-model.number="def.resume.hours"
          type="number"
          min="0.1"
          step="0.5"
          class="input !w-24"
          :readonly="readonly"
          :disabled="def.resume.mode !== 'within_hours'"
          :aria-label="t('editor.leave.resume_hours')"
        />
        <span>{{ t('editor.hours') }}</span>
      </label>
      <p class="hint">{{ t('editor.leave.resume_hint') }}</p>
    </fieldset>

    <div v-if="['all_fields', 'explicit_yes'].includes(def.completion)">
      <label class="label" :for="`${uid}-reopen`">{{ t('editor.leave.reopen') }}</label>
      <div class="flex flex-wrap items-center gap-2.5">
        <input
          :id="`${uid}-reopen`"
          v-model.number="def.reopen_hours"
          type="number"
          min="0"
          max="720"
          step="1"
          class="input !w-24"
          placeholder="24"
          :readonly="readonly"
        />
        <span>{{ t('editor.hours') }}</span>
      </div>
      <p class="hint">{{ t('editor.leave.reopen_hint') }}</p>
    </div>

    <div v-if="others.length">
      <p class="label">{{ t('editor.leave.incompatible') }}</p>
      <div class="flex flex-wrap gap-1.5">
        <button
          v-for="s in others"
          :key="s.key"
          type="button"
          class="chip"
          :class="def.incompatible_with.includes(s.key) ? 'chip-on' : ''"
          :aria-pressed="def.incompatible_with.includes(s.key)"
          :disabled="readonly"
          @click="toggle(s.key)"
        >
          {{ s.title || s.key }}
        </button>
      </div>
      <p class="hint">{{ t('editor.leave.incompatible_hint') }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{
  def: any
  languages: string[]
  // the tenant's other scenarios: [{key, title}]
  scenarios?: { key: string; title?: string | null }[]
  readonly?: boolean
}>()
const { t } = useI18n()
const uid = useId()

// the tenant's other scenarios, and any key the scenario already names
const others = computed(() => {
  const out = (props.scenarios || []).filter((s) => s.key !== props.def.key)
  for (const k of props.def.incompatible_with || []) if (!out.some((s) => s.key === k)) out.push({ key: k })
  return out
})

function toggle(k: string) {
  const list = props.def.incompatible_with as string[]
  const i = list.indexOf(k)
  if (i >= 0) list.splice(i, 1)
  else list.push(k)
}
</script>
