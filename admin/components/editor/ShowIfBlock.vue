<template>
  <div class="rounded-button border border-slate-200 dark:border-slate-700 p-3 space-y-3">
    <!-- «when to ask this question» (show_if): yes/no signs over the client's answers, built
         like the start of a scenario — «this is enough» / «only together with another»,
         the exact points and the threshold one tap away -->
    <div class="flex flex-wrap items-center gap-2">
      <p class="label !mb-0 flex-1">{{ t('editor.show.title') }}</p>
      <button v-if="!readonly" type="button" class="btn-ghost btn-sm" @click="emit('remove')">{{ t('editor.show.remove') }}</button>
    </div>
    <p class="text-sm text-slate-600 dark:text-slate-300">{{ t('editor.show.lead') }}</p>

    <div v-for="(q, i) in (show.questions as any[])" :key="i" class="rounded-button border border-slate-200 dark:border-slate-700 py-2 pl-3.5 pr-2 space-y-2">
      <div class="flex flex-wrap items-start gap-2.5">
        <LocalizedInput
          v-model="q.ask"
          class="flex-1 basis-[260px] min-w-0"
          :languages="languages"
          :main="def.language"
          multiline
          :rows="2"
          :readonly="readonly"
          :aria-label="t('editor.show.sign_n', { n: i + 1 })"
          :placeholder="t('editor.show.placeholder')"
        />
        <select
          v-if="show.questions.length > 1"
          class="select !w-auto"
          :value="signEnough(show, q) ? 'enough' : 'together'"
          :disabled="readonly"
          :aria-label="t('editor.start.how_much')"
          @change="setSignEnough(show, q, ($event.target as HTMLSelectElement).value === 'enough')"
        >
          <option value="enough">{{ t('editor.start.enough') }}</option>
          <option value="together">{{ t('editor.start.together') }}</option>
        </select>
        <button
          v-if="!readonly"
          type="button"
          class="btn-icon btn-ghost"
          :disabled="show.questions.length < 2"
          :aria-label="t('editor.show.remove_sign')"
          :title="t('editor.show.remove_sign')"
          @click="removeSign(i)"
        >
          <UiIcon name="close" :size="18" />
        </button>
      </div>
      <div v-if="exact || ctx?.showKeys.value" class="flex flex-wrap items-center gap-x-5 gap-y-2 pb-1 text-sm">
        <label v-if="exact" class="flex items-center gap-2">
          {{ t('editor.start.points') }}
          <input v-model.number="q.weight" type="number" min="1" max="100" class="input !h-9 !w-20" :readonly="readonly" />
        </label>
        <label v-if="ctx?.showKeys.value" class="flex items-center gap-2">
          {{ t('editor.key') }}
          <input v-model.trim="q.key" class="input !h-9 !w-44 code" :readonly="readonly" />
        </label>
      </div>
    </div>
    <p class="hint !mt-1">{{ t('editor.show.hint') }}</p>
    <button v-if="!readonly" type="button" class="btn-dashed btn-sm" @click="addSign">
      <UiIcon name="plus" :size="14" /> {{ t('editor.show.add_sign') }}
    </button>

    <!-- how it works out -->
    <div v-if="show.questions.length > 1" class="rounded-button bg-slate-50 dark:bg-slate-900/40 px-4 py-3 space-y-2 text-sm">
      <p class="text-[13px] font-semibold text-slate-600 dark:text-slate-300">{{ t('editor.start.result') }}</p>
      <p v-if="never" class="text-rose-700 dark:text-rose-300">{{ t('editor.show.never') }}</p>
      <template v-else>
        <div v-for="(set, i) in sets" :key="`y${i}`" class="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
          <span class="shrink-0 basis-[92px] rounded-full py-px text-center text-[13px] font-semibold bg-[#e4f3ea] text-[#17663f] dark:bg-emerald-500/15 dark:text-emerald-300">{{ t('editor.show.will') }}</span>
          <span class="flex-1 basis-[220px] min-w-0">{{ set.map(label).join(` ${t('editor.start.and')} `) }}</span>
        </div>
        <div v-for="i in alone" :key="`n${i}`" class="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
          <span class="shrink-0 basis-[92px] rounded-full py-px text-center text-[13px] font-semibold bg-[#e6e9f2] text-slate-600 dark:bg-slate-700 dark:text-slate-300">{{ t('editor.show.will_not') }}</span>
          <span class="flex-1 basis-[220px] min-w-0">{{ t('editor.show.only', { sign: label(i) }) }}</span>
        </div>
      </template>
    </div>

    <div class="grid gap-4 sm:grid-cols-2">
      <div>
        <label class="label" :for="`${uid}-unknown`">{{ t('editor.show.unknown') }}</label>
        <select :id="`${uid}-unknown`" v-model="show.on_unknown" class="select" :disabled="readonly">
          <option value="ask">{{ t('editor.show.unknown_ask') }}</option>
          <option value="skip">{{ t('editor.show.unknown_skip') }}</option>
        </select>
        <p class="hint">{{ t('editor.show.unknown_hint') }}</p>
      </div>
      <div>
        <label class="label" :for="`${uid}-recheck`">{{ t('editor.show.recheck') }}</label>
        <select :id="`${uid}-recheck`" v-model="show.recheck" class="select" :disabled="readonly">
          <option value="every_turn">{{ t('editor.show.recheck_every_turn') }}</option>
          <option value="once">{{ t('editor.show.recheck_once') }}</option>
        </select>
        <p class="hint">{{ t('editor.show.recheck_hint') }}</p>
      </div>
    </div>

    <div v-if="show.questions.length > 1">
      <button type="button" class="link text-sm" :aria-expanded="exact" @click="exact = !exact">
        {{ t(exact ? 'editor.start.exact_hide' : 'editor.start.exact_show') }}
      </button>
      <div v-if="exact" class="mt-2 rounded-button border border-slate-200 dark:border-slate-700 p-4 space-y-3 text-sm">
        <p class="text-slate-600 dark:text-slate-300">{{ t('editor.show.exact_hint') }}</p>
        <label class="flex flex-wrap items-center gap-2">
          <span class="font-semibold text-slate-900 dark:text-white">{{ t('editor.start.threshold') }}</span>
          <input v-model.number="show.threshold" type="number" min="1" class="input !h-9 !w-20" :readonly="readonly" />
          <span class="muted">{{ t('editor.start.of_total', { total }) }}</span>
        </label>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { setSignEnough, signEnough, slugKey, startSets, startThreshold, startTotal } from '~/utils/scenario'
import { pickText } from '~/utils/texts'

const props = defineProps<{ def: any; show: any; languages: string[]; readonly?: boolean }>()
const emit = defineEmits<{ remove: [] }>()
const { t } = useI18n()
const ctx = useEditorContext()
const uid = useId()

const exact = ref(false)
const lang = computed(() => ctx?.lang.value || props.def.language)
const total = computed(() => startTotal(props.show))
const never = computed(() => startThreshold(props.show) > total.value)
const sets = computed(() => startSets(props.show))
// signs that do not let the question be asked on their own
const alone = computed(() =>
  (props.show.questions as any[])
    .map((q, i) => (signEnough(props.show, q) ? -1 : i))
    .filter((i) => i >= 0)
    .slice(0, 3),
)

function label(i: number): string {
  const value = pickText(props.show.questions[i]?.ask, lang.value, props.def.language).trim()
  if (!value) return t('editor.show.sign_n', { n: i + 1 })
  return `«${value.length > 80 ? `${value.slice(0, 80)}…` : value}»`
}

function addSign() {
  const keys = (props.show.questions as any[]).map((q) => q.key)
  const q: any = { key: slugKey('', keys, 'sign'), ask: {}, weight: 1 }
  props.show.questions.push(q)
  setSignEnough(props.show, q, true)
}

function removeSign(i: number) {
  props.show.questions.splice(i, 1)
  // one sign left: it decides alone, as the format reads a single sign without a threshold
  if (props.show.questions.length === 1) {
    props.show.threshold = Number(props.show.questions[0].weight) || 1
  }
}
</script>
