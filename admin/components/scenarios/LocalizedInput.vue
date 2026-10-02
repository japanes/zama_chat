<template>
  <!-- a scenario text: one tab per bot language; a red dot marks a missing translation -->
  <div>
    <div v-if="langs.length > 1" class="flex flex-wrap gap-1 mb-1">
      <button
        v-for="l in langs"
        :key="l"
        type="button"
        class="relative px-2 py-0.5 rounded-badge text-xs font-medium uppercase border"
        :class="l === current
          ? 'border-primary bg-primary/10 text-primary'
          : 'border-slate-200 dark:border-slate-700 text-slate-500 hover:text-slate-800 dark:hover:text-slate-200'"
        @click="current = l"
      >
        {{ l }}<span v-if="l === main" class="ml-0.5 opacity-60">*</span>
        <span
          v-if="!readonly && missing.includes(l) && !(optional && empty)"
          class="absolute -top-1 -right-1 w-2 h-2 rounded-full bg-rose-500"
          :title="t('scenarios.text_missing')"
        />
      </button>
    </div>
    <textarea
      v-if="multiline"
      :value="valueFor(current)"
      :rows="rows"
      class="textarea"
      :readonly="readonly"
      :placeholder="placeholderFor(current)"
      @input="set(current, ($event.target as HTMLTextAreaElement).value)"
    />
    <input
      v-else
      :value="valueFor(current)"
      class="input"
      :readonly="readonly"
      :placeholder="placeholderFor(current)"
      @input="set(current, ($event.target as HTMLInputElement).value)"
    />
  </div>
</template>

<script setup lang="ts">
import { missingLanguages, type LText } from '~/utils/scenario'

const props = withDefaults(
  defineProps<{
    modelValue?: LText | null
    languages: string[]
    main: string
    multiline?: boolean
    rows?: number
    optional?: boolean
    readonly?: boolean
    placeholder?: string
  }>(),
  { modelValue: null, rows: 2, placeholder: '' },
)
const emit = defineEmits<{ 'update:modelValue': [value: LText | null] }>()
const { t } = useI18n()

// tenant languages first, then any other language the text already has
const langs = computed(() => {
  const out = [...props.languages]
  if (props.main && !out.includes(props.main)) out.unshift(props.main)
  if (props.modelValue && typeof props.modelValue === 'object') {
    for (const l of Object.keys(props.modelValue)) if (!out.includes(l)) out.push(l)
  }
  return out.length ? out : [props.main || 'en']
})
const current = ref(langs.value.includes(props.main) ? props.main : langs.value[0])
watch(langs, (value) => {
  if (!value.includes(current.value)) current.value = value[0]
})

const asDict = computed<Record<string, string>>(() => {
  const v = props.modelValue
  if (typeof v === 'string') return { [props.main]: v }
  return v && typeof v === 'object' ? v : {}
})
const missing = computed(() => missingLanguages(props.modelValue, props.languages, props.main))
const empty = computed(() => !Object.values(asDict.value).some((x) => String(x || '').trim()))

function valueFor(lang: string) {
  return asDict.value[lang] || ''
}
function placeholderFor(lang: string) {
  // the main-language text as a hint for the translation
  if (lang !== props.main && asDict.value[props.main]) return asDict.value[props.main]
  return props.placeholder
}
function set(lang: string, value: string) {
  const next = { ...asDict.value, [lang]: value }
  if (!value) delete next[lang]
  emit('update:modelValue', Object.keys(next).length ? next : null)
}
</script>
