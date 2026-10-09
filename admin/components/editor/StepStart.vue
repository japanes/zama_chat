<template>
  <div class="space-y-3">
    <!-- step 1: the signs that start the scenario. A person picks «this is enough» or
         «only together with another»; the exact points and the threshold are one tap away -->
    <p class="text-slate-600 dark:text-slate-300">{{ t('editor.start.lead') }}</p>

    <div
      v-for="(q, i) in (def.start.questions as any[])"
      :key="i"
      class="rounded-button border border-slate-200 dark:border-slate-700 py-2 pl-3.5 pr-2 space-y-2"
    >
      <div class="flex flex-wrap items-start gap-2.5">
        <div class="flex-1 basis-[260px] min-w-0">
          <LocalizedInput
            v-if="q.field === undefined"
            v-model="q.ask"
            :languages="languages"
            :main="def.language"
            multiline
            :rows="2"
            :readonly="readonly"
            :aria-label="t('editor.start.sign_n', { n: i + 1 })"
            :placeholder="t('editor.start.own_placeholder')"
          />
          <div v-else class="flex flex-wrap items-center gap-2 min-h-[44px]">
            <span>{{ t('editor.start.names') }}</span>
            <select v-model="q.field" class="select !w-auto max-w-full" :disabled="readonly" :aria-label="t('editor.start.which_question')">
              <option v-for="f in fields" :key="f.key" :value="f.key">{{ name(f) }}</option>
            </select>
            <span class="text-sm muted">{{ t('editor.start.names_hint') }}</span>
          </div>
        </div>
        <select
          class="select !w-auto"
          :value="signEnough(def.start, q) ? 'enough' : 'together'"
          :disabled="readonly"
          :aria-label="t('editor.start.how_much')"
          @change="setSignEnough(def.start, q, ($event.target as HTMLSelectElement).value === 'enough')"
        >
          <option value="enough">{{ t('editor.start.enough') }}</option>
          <option value="together">{{ t('editor.start.together') }}</option>
        </select>
        <button
          v-if="!readonly"
          type="button"
          class="btn-icon btn-ghost"
          :disabled="def.start.questions.length < 2"
          :aria-label="t('editor.start.remove')"
          :title="t('editor.start.remove')"
          @click="def.start.questions.splice(i, 1)"
        >
          <UiIcon name="close" :size="18" />
        </button>
      </div>
      <div v-if="exact || (ctx?.showKeys.value && q.field === undefined)" class="flex flex-wrap items-center gap-x-5 gap-y-2 pb-1 text-sm">
        <label v-if="exact" class="flex items-center gap-2">
          {{ t('editor.start.points') }}
          <input v-model.number="q.weight" type="number" min="1" max="100" class="input !h-9 !w-20" :readonly="readonly" />
        </label>
        <label v-if="ctx?.showKeys.value && q.field === undefined" class="flex items-center gap-2">
          {{ t('editor.key') }}
          <input v-model.trim="q.key" class="input !h-9 !w-44 code" :readonly="readonly" />
        </label>
      </div>
    </div>

    <div v-if="!readonly" class="flex flex-wrap gap-2">
      <button type="button" class="btn-dashed" @click="addOwn">
        <UiIcon name="plus" :size="16" /> {{ t('editor.start.add') }}
      </button>
      <button type="button" class="btn-dashed" :disabled="!freeFields.length" @click="addField">
        <UiIcon name="plus" :size="16" /> {{ t('editor.start.add_field') }}
      </button>
    </div>

    <!-- how it works out -->
    <div class="rounded-button bg-slate-50 dark:bg-slate-900/40 px-4 py-3.5 space-y-2">
      <p class="text-[13px] font-semibold text-slate-600 dark:text-slate-300">{{ t('editor.start.result') }}</p>
      <p v-if="never" class="text-rose-700 dark:text-rose-300">{{ t('editor.problem.never') }}</p>
      <template v-else>
        <div v-for="(set, i) in sets" :key="`y${i}`" class="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
          <span class="shrink-0 basis-[92px] rounded-full py-px text-center text-[13px] font-semibold bg-[#e4f3ea] text-[#17663f] dark:bg-emerald-500/15 dark:text-emerald-300">{{ t('editor.start.will') }}</span>
          <span class="flex-1 basis-[220px] min-w-0">{{ set.map(label).join(` ${t('editor.start.and')} `) }}<template v-if="set.length > 1"> — {{ t('editor.start.at_once') }}</template></span>
        </div>
        <div v-for="i in alone" :key="`n${i}`" class="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
          <span class="shrink-0 basis-[92px] rounded-full py-px text-center text-[13px] font-semibold bg-[#e6e9f2] text-slate-600 dark:bg-slate-700 dark:text-slate-300">{{ t('editor.start.will_not') }}</span>
          <span class="flex-1 basis-[220px] min-w-0">{{ t('editor.start.only', { sign: label(i) }) }}</span>
        </div>
      </template>
    </div>

    <!-- the exact numbers behind the simple choice -->
    <div>
      <button type="button" class="link text-sm" :aria-expanded="exact" @click="exact = !exact">
        {{ t(exact ? 'editor.start.exact_hide' : 'editor.start.exact_show') }}
      </button>
      <div v-if="exact" class="mt-2 rounded-button border border-slate-200 dark:border-slate-700 p-4 space-y-3 text-sm">
        <p class="text-slate-600 dark:text-slate-300">{{ t('editor.start.exact_hint') }}</p>
        <label class="flex flex-wrap items-center gap-2">
          <span class="font-semibold text-slate-900 dark:text-white">{{ t('editor.start.threshold') }}</span>
          <input v-model.number="def.start.threshold" type="number" min="1" class="input !h-9 !w-20" :readonly="readonly" />
          <span class="muted">{{ t('editor.start.of_total', { total }) }}</span>
        </label>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { fieldName, setSignEnough, signEnough, signLabel, slugKey, startSets, startThreshold, startTotal } from '~/utils/scenario'

const props = defineProps<{ def: any; languages: string[]; readonly?: boolean }>()
const { t } = useI18n()
const ctx = useEditorContext()

const exact = ref(false)
const lang = computed(() => ctx?.lang.value || props.def.language)
const fields = computed(() => (props.def.fields as any[]).filter((f) => f.key))
const used = computed(() => (props.def.start.questions as any[]).map((q) => q.field).filter(Boolean))
const freeFields = computed(() => fields.value.filter((f) => !used.value.includes(f.key)))
const total = computed(() => startTotal(props.def.start))
const never = computed(() => startThreshold(props.def.start) > total.value)
const sets = computed(() => startSets(props.def.start))
// signs that do not start the scenario on their own
const alone = computed(() =>
  (props.def.start.questions as any[])
    .map((q, i) => (signEnough(props.def.start, q) ? -1 : i))
    .filter((i) => i >= 0)
    .slice(0, 3),
)

function name(f: any): string {
  return fieldName(f, lang.value, props.def.language) || t('editor.q.untitled')
}

function label(i: number): string {
  return signLabel(props.def, i, lang.value, t)
}

function addOwn() {
  const keys = (props.def.start.questions as any[]).map((q) => q.key).filter(Boolean)
  const q: any = { key: slugKey('', keys, 'sign'), ask: {}, weight: 1 }
  props.def.start.questions.push(q)
  // a new sign is «enough» until the person says otherwise
  setSignEnough(props.def.start, q, true)
}

function addField() {
  const q: any = { field: freeFields.value[0].key, weight: 1 }
  props.def.start.questions.push(q)
  // «names the service» alone is rarely a wish to book
  setSignEnough(props.def.start, q, false)
}
</script>
