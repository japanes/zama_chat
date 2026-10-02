<template>
  <!-- bot languages and the forced rule "client language → reply language" -->
  <div class="space-y-4">
    <div>
      <label class="label">{{ t('settings.languages') }}</label>
      <div class="flex flex-wrap items-center gap-1.5">
        <span v-for="(l, i) in languages" :key="l" class="badge-blue code inline-flex items-center gap-1">
          {{ l }}<span v-if="i === 0" class="opacity-60">({{ t('settings.primary') }})</span>
          <button v-if="!readonly" type="button" class="hover:text-rose-600" :disabled="i === 0" :title="t('settings.make_primary')" @click="makePrimary(i)">
            <UiIcon v-if="i > 0" name="chevron-up" :size="12" />
          </button>
          <button v-if="!readonly" type="button" class="hover:text-rose-600" :aria-label="t('common.delete')" @click="removeLanguage(l)">
            <UiIcon name="close" :size="12" />
          </button>
        </span>
        <form v-if="!readonly" class="flex items-center gap-1" @submit.prevent="addLanguage">
          <input v-model.trim="newLang" class="input h-7 w-24 code" :placeholder="t('settings.language_code')" list="lang-codes" />
          <button type="submit" class="btn-ghost btn-sm" :disabled="!newLang">
            <UiIcon name="plus" :size="14" />
          </button>
        </form>
        <datalist id="lang-codes">
          <option v-for="c in COMMON_LANGS" :key="c" :value="c" />
        </datalist>
      </div>
      <p class="hint">{{ t('settings.languages_hint') }}</p>
    </div>

    <div>
      <label class="label">{{ t('settings.reply_rules') }}</label>
      <div class="space-y-2">
        <div v-for="(r, i) in rules" :key="i" class="flex items-center gap-2">
          <input v-model.trim="r.source" class="input w-28 code" :placeholder="t('settings.client_language')" :readonly="readonly" @change="emitRules" />
          <UiIcon name="chevron-right" :size="16" class="text-slate-400" />
          <select v-model="r.target" class="select w-28" :disabled="readonly" @change="emitRules">
            <option v-for="l in languages" :key="l" :value="l">{{ l }}</option>
          </select>
          <button v-if="!readonly" type="button" class="btn-icon btn-ghost btn-sm text-rose-600" :aria-label="t('common.delete')" @click="removeRule(i)">
            <UiIcon name="trash" :size="14" />
          </button>
        </div>
      </div>
      <button v-if="!readonly" type="button" class="btn-ghost btn-sm mt-2" :disabled="!languages.length" @click="addRule">
        <UiIcon name="plus" :size="14" /> {{ t('settings.add_rule') }}
      </button>
      <p class="hint">{{ t('settings.reply_rules_hint') }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{ languages: string[]; replyLanguage: Record<string, string>; readonly?: boolean }>()
const emit = defineEmits<{
  'update:languages': [value: string[]]
  'update:replyLanguage': [value: Record<string, string>]
}>()
const { t } = useI18n()

const COMMON_LANGS = ['en', 'uk', 'ru', 'pl', 'de', 'fr', 'es', 'it', 'pt', 'tr', 'ro', 'cs', 'kk', 'ka', 'he', 'ar']
const LANG_RE = /^[a-z]{2,3}(-[a-z0-9]{2,8})?$/

const newLang = ref('')
const rules = ref<{ source: string; target: string }[]>([])
watch(
  () => props.replyLanguage,
  (value) => {
    rules.value = Object.entries(value || {}).map(([source, target]) => ({ source, target }))
  },
  { immediate: true },
)

function addLanguage() {
  const code = newLang.value.toLowerCase()
  if (!LANG_RE.test(code) || props.languages.includes(code)) return
  emit('update:languages', [...props.languages, code])
  newLang.value = ''
}
function removeLanguage(code: string) {
  emit('update:languages', props.languages.filter((l) => l !== code))
}
function makePrimary(i: number) {
  const next = [...props.languages]
  const [code] = next.splice(i, 1)
  emit('update:languages', [code, ...next])
}
function addRule() {
  rules.value.push({ source: rules.value.some((r) => r.source === '*') ? '' : '*', target: props.languages[0] })
  emitRules()
}
function removeRule(i: number) {
  rules.value.splice(i, 1)
  emitRules()
}
function emitRules() {
  const out: Record<string, string> = {}
  for (const r of rules.value) if (r.source && r.target) out[r.source.toLowerCase()] = r.target
  emit('update:replyLanguage', out)
}
</script>
