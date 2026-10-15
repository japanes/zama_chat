<template>
  <div class="rounded-button border" :class="open ? 'border-primary-dark dark:border-primary-light' : 'border-slate-200 dark:border-slate-700'">
    <!-- one question of the scenario: a row in the list, or the whole form when open -->
    <div class="flex items-center gap-2 py-1.5 pl-3 pr-1.5">
      <span class="grid place-items-center w-7 h-7 shrink-0 rounded-full bg-[#e6e9f2] dark:bg-slate-700 text-sm font-semibold text-slate-900 dark:text-white">{{ index + 1 }}</span>
      <button
        type="button"
        class="flex-1 min-w-0 flex flex-wrap items-center gap-x-3 gap-y-0.5 min-h-[44px] py-1 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-primary rounded-md"
        :aria-expanded="open"
        @click="emit('toggle')"
      >
        <span class="min-w-0 flex-1 basis-[240px]">
          <span class="block font-semibold text-slate-900 dark:text-white truncate">{{ name || t('editor.q.untitled') }}</span>
          <span v-if="!open" class="block text-sm muted truncate">{{ questionText || t('editor.q.no_text') }}</span>
        </span>
        <span v-if="!open" class="shrink-0 text-sm muted">{{ summary }}</span>
      </button>
      <template v-if="!readonly">
        <button type="button" class="btn-icon btn-ghost btn-sm" :disabled="index === 0" :aria-label="t('common.move_up')" :title="t('common.move_up')" @click="emit('move', -1)">
          <UiIcon name="chevron-up" :size="16" />
        </button>
        <button type="button" class="btn-icon btn-ghost btn-sm" :disabled="last" :aria-label="t('common.move_down')" :title="t('common.move_down')" @click="emit('move', 1)">
          <UiIcon name="chevron-down" :size="16" />
        </button>
      </template>
      <button
        type="button"
        class="btn-icon btn-ghost"
        :aria-label="t(open ? 'editor.q.close' : 'editor.q.open', { name: name || index + 1 })"
        :aria-expanded="open"
        @click="emit('toggle')"
      >
        <UiIcon :name="open ? 'close' : 'edit'" :size="18" />
      </button>
    </div>

    <div v-if="open" class="px-4 pb-4 pt-2 space-y-4 border-t border-slate-200 dark:border-slate-700">
      <div class="grid gap-4 sm:grid-cols-2">
        <div>
          <label class="label" :for="`${uid}-title`">{{ t('editor.q.title') }}</label>
          <LocalizedInput
            :model-value="field.title"
            :input-id="`${uid}-title`"
            :languages="languages"
            :main="def.language"
            optional
            :readonly="readonly"
            :placeholder="t('editor.q.title_placeholder')"
            @update:model-value="setTitle"
          />
          <p class="hint">{{ t('editor.q.title_hint') }}</p>
        </div>
        <div>
          <label class="label" :for="`${uid}-type`">{{ t('editor.q.type') }}</label>
          <select :id="`${uid}-type`" v-model="field.type" class="select" :disabled="readonly">
            <option v-for="ft in FIELD_TYPES" :key="ft" :value="ft">{{ t(`editor.type.${ft}`) }}</option>
          </select>
        </div>
      </div>

      <div>
        <label class="label" :for="`${uid}-ask`">{{ t('editor.q.ask') }}</label>
        <LocalizedInput v-model="field.question" :input-id="`${uid}-ask`" :languages="languages" :main="def.language" multiline :readonly="readonly" />
        <p class="hint">{{ t('editor.q.ask_hint') }}</p>
      </div>

      <!-- choice: where the options come from -->
      <div v-if="field.type === 'choice'">
        <p class="label">{{ t('editor.q.options_source') }}</p>
        <div class="flex flex-wrap gap-x-5">
          <label class="flex items-center gap-2 min-h-[44px]">
            <input type="radio" class="accent-primary-dark" :name="`${uid}-source`" :checked="field.options_from !== 'caller'" :disabled="readonly" @change="field.options_from = null" />
            {{ t('editor.q.options_here') }}
          </label>
          <label class="flex items-center gap-2 min-h-[44px]">
            <input type="radio" class="accent-primary-dark" :name="`${uid}-source`" :checked="field.options_from === 'caller'" :disabled="readonly" @change="field.options_from = 'caller'" />
            {{ t('editor.q.options_caller') }}
          </label>
        </div>
        <p v-if="field.options_from === 'caller'" class="hint">{{ t('editor.q.options_caller_hint') }}</p>
      </div>

      <!-- choice: the options -->
      <div v-if="field.type === 'choice' && field.options_from !== 'caller'">
        <p class="label">{{ t('editor.q.options') }}</p>
        <div class="space-y-2">
          <div v-for="(o, i) in (field.options as any[])" :key="i" class="flex flex-wrap items-start gap-2">
            <LocalizedInput
              :model-value="o.label"
              class="flex-1 min-w-[12rem]"
              :languages="languages"
              :main="def.language"
              :readonly="readonly"
              :aria-label="t('editor.q.option_n', { n: i + 1 })"
              :placeholder="t('editor.q.option_n', { n: i + 1 })"
              @update:model-value="setLabel(o, field.options, $event, 'option')"
            />
            <input v-if="ctx?.showKeys.value" v-model.trim="o.key" class="input !w-36 code" :aria-label="t('editor.key')" :readonly="readonly" @input="o._auto = false" />
            <button v-if="!readonly" type="button" class="btn-icon btn-ghost" :aria-label="t('common.delete')" @click="field.options.splice(i, 1)">
              <UiIcon name="close" :size="18" />
            </button>
          </div>
        </div>
        <button v-if="!readonly" type="button" class="btn-dashed btn-sm mt-2" @click="addOption">
          <UiIcon name="plus" :size="14" /> {{ t('scenarios.add_option') }}
        </button>
      </div>

      <!-- an item of the catalog -->
      <div v-if="field.type === 'catalog'" class="grid gap-4 sm:grid-cols-[1fr_11rem]">
        <div>
          <label class="label" :for="`${uid}-cats`">{{ t('editor.q.catalog_categories') }}</label>
          <input :id="`${uid}-cats`" v-model="categoriesText" class="input" :readonly="readonly" :list="`${uid}-catlist`" :placeholder="t('editor.q.catalog_all')" />
          <datalist :id="`${uid}-catlist`">
            <option v-for="c in categories" :key="c" :value="c" />
          </datalist>
          <p class="hint">{{ t('scenarios.catalog_categories_hint') }}</p>
        </div>
        <div>
          <label class="label" :for="`${uid}-max`">{{ t('editor.q.catalog_max') }}</label>
          <input :id="`${uid}-max`" v-model.number="field.catalog.max_options" type="number" min="2" max="10" class="input" :readonly="readonly" />
        </div>
        <details class="sm:col-span-2 text-sm">
          <summary class="cursor-pointer muted">{{ t('editor.q.catalog_how') }}</summary>
          <p class="mt-1 text-slate-600 dark:text-slate-300">{{ t('scenarios.catalog_max_options_hint') }}</p>
        </details>
      </div>

      <!-- date -->
      <div v-if="field.type === 'date'" class="grid gap-4 sm:grid-cols-2">
        <div>
          <label class="label" :for="`${uid}-days`">{{ t('scenarios.date_max_days') }}</label>
          <input :id="`${uid}-days`" v-model="field.date.max_days_ahead" type="number" min="0" max="3650" class="input" :placeholder="t('scenarios.no_limit')" :readonly="readonly" />
        </div>
        <label class="flex items-center gap-2 min-h-[44px] sm:mt-7">
          <input v-model="field.date.allow_past" type="checkbox" class="checkbox" :disabled="readonly" />
          {{ t('scenarios.date_allow_past') }}
        </label>
      </div>

      <!-- number -->
      <div v-if="field.type === 'number'" class="grid gap-4 sm:grid-cols-3">
        <div>
          <label class="label" :for="`${uid}-min`">{{ t('scenarios.number_min') }}</label>
          <input :id="`${uid}-min`" v-model="field.number.min" type="number" class="input" :placeholder="t('scenarios.no_limit')" :readonly="readonly" />
        </div>
        <div>
          <label class="label" :for="`${uid}-nmax`">{{ t('scenarios.number_max') }}</label>
          <input :id="`${uid}-nmax`" v-model="field.number.max" type="number" class="input" :placeholder="t('scenarios.no_limit')" :readonly="readonly" />
        </div>
        <label class="flex items-center gap-2 min-h-[44px] sm:mt-7">
          <input v-model="field.number.integer" type="checkbox" class="checkbox" :disabled="readonly" />
          {{ t('scenarios.number_integer') }}
        </label>
      </div>

      <!-- list -->
      <div v-if="field.type === 'list'" class="space-y-3">
        <div class="grid gap-4 sm:grid-cols-2">
          <div>
            <label class="label" :for="`${uid}-lmin`">{{ t('scenarios.list_min') }}</label>
            <input :id="`${uid}-lmin`" v-model.number="field.items.min_items" type="number" min="1" max="50" class="input" :readonly="readonly" />
          </div>
          <div>
            <label class="label" :for="`${uid}-lmax`">{{ t('scenarios.list_max') }}</label>
            <input :id="`${uid}-lmax`" v-model.number="field.items.max_items" type="number" min="1" max="50" class="input" :readonly="readonly" />
          </div>
        </div>
        <div>
          <p class="label">{{ t('scenarios.list_item_fields') }}</p>
          <p class="hint !mt-0 mb-2">{{ t('scenarios.list_item_fields_hint') }}</p>
          <div v-for="(x, i) in (field.items.item_fields as any[])" :key="i" class="flex flex-wrap items-start gap-2 mb-2">
            <LocalizedInput
              :model-value="x.label"
              class="flex-1 min-w-[12rem]"
              :languages="languages"
              :main="def.language"
              :readonly="readonly"
              :aria-label="t('editor.q.part_n', { n: i + 1 })"
              :placeholder="t('editor.q.part_n', { n: i + 1 })"
              @update:model-value="setLabel(x, field.items.item_fields, $event, 'part')"
            />
            <input v-if="ctx?.showKeys.value" v-model.trim="x.key" class="input !w-36 code" :aria-label="t('editor.key')" :readonly="readonly" @input="x._auto = false" />
            <label class="flex items-center gap-1.5 min-h-[44px] text-sm">
              <input v-model="x.required" type="checkbox" class="checkbox" :disabled="readonly" />
              {{ t('scenarios.required') }}
            </label>
            <button v-if="!readonly" type="button" class="btn-icon btn-ghost" :aria-label="t('common.delete')" @click="field.items.item_fields.splice(i, 1)">
              <UiIcon name="close" :size="18" />
            </button>
          </div>
          <button v-if="!readonly" type="button" class="btn-dashed btn-sm" @click="addPart">
            <UiIcon name="plus" :size="14" /> {{ t('scenarios.add_item_field') }}
          </button>
        </div>
      </div>

      <!-- a photo or a file -->
      <div v-if="field.type === 'attachment'" class="grid gap-4 sm:grid-cols-[1fr_11rem]">
        <div>
          <p class="label">{{ t('scenarios.attachment_kinds') }}</p>
          <div class="flex flex-wrap gap-x-4">
            <label v-for="k in ATTACHMENT_KINDS" :key="k" class="flex items-center gap-1.5 min-h-[44px]">
              <input type="checkbox" class="checkbox" :checked="field.attachment.kinds.includes(k)" :disabled="readonly" @change="toggleKind(k)" />
              {{ t(`scenarios.attachment_kind.${k}`) }}
            </label>
          </div>
        </div>
        <div>
          <label class="label" :for="`${uid}-amax`">{{ t('scenarios.attachment_max') }}</label>
          <input :id="`${uid}-amax`" v-model.number="field.attachment.max_items" type="number" min="1" max="10" class="input" :readonly="readonly" />
        </div>
      </div>

      <!-- a payment receipt -->
      <div v-if="field.type === 'receipt'" class="space-y-3">
        <p class="alert-blue">{{ t('editor.q.receipt_hint') }}</p>
        <div class="grid gap-4 sm:grid-cols-3">
          <div>
            <label class="label" :for="`${uid}-ramount`">{{ t('scenarios.receipt_amount') }}</label>
            <input :id="`${uid}-ramount`" v-model="field.receipt.amount" type="number" min="0" step="0.01" class="input" :readonly="readonly" />
          </div>
          <div>
            <label class="label" :for="`${uid}-rfield`">{{ t('editor.q.receipt_amount_field') }}</label>
            <select :id="`${uid}-rfield`" v-model="field.receipt.amount_field" class="select" :disabled="readonly">
              <option value="">—</option>
              <option v-for="f in fieldsOf('catalog')" :key="f.key" :value="f.key">{{ nameOf(f) }}</option>
            </select>
          </div>
          <div>
            <label class="label" :for="`${uid}-rqty`">{{ t('editor.q.receipt_quantity_field') }}</label>
            <select :id="`${uid}-rqty`" v-model="field.receipt.quantity_field" class="select" :disabled="readonly">
              <option value="">—</option>
              <option v-for="f in fieldsOf('number')" :key="f.key" :value="f.key">{{ nameOf(f) }}</option>
            </select>
          </div>
          <div>
            <label class="label" :for="`${uid}-rcur`">{{ t('scenarios.receipt_currency') }}</label>
            <input :id="`${uid}-rcur`" v-model.trim="field.receipt.currency" class="input code uppercase" maxlength="3" placeholder="UAH" :readonly="readonly" />
          </div>
          <div>
            <label class="label" :for="`${uid}-rage`">{{ t('scenarios.receipt_max_age') }}</label>
            <input :id="`${uid}-rage`" v-model.number="field.receipt.max_age_days" type="number" min="0" max="365" class="input" :readonly="readonly" />
          </div>
          <div>
            <label class="label" :for="`${uid}-rtol`">{{ t('scenarios.receipt_tolerance') }}</label>
            <input :id="`${uid}-rtol`" v-model="field.receipt.tolerance" type="number" min="0" step="0.01" class="input" :readonly="readonly" />
          </div>
        </div>
        <div>
          <label class="label" :for="`${uid}-rrec`">{{ t('scenarios.receipt_recipients') }}</label>
          <input :id="`${uid}-rrec`" v-model="recipientsText" class="input" :readonly="readonly" />
          <p class="hint">{{ t('scenarios.receipt_recipients_hint') }}</p>
        </div>
      </div>

      <!-- conditions: the answer is taken only when every one of them holds -->
      <div v-if="!isFile && (field.filters.length || showFilters)">
        <p class="label">{{ t('editor.q.filters') }}</p>
        <div class="space-y-2">
          <div v-for="(flt, i) in (field.filters as any[])" :key="i" class="rounded-button border border-slate-200 dark:border-slate-700 p-2 pl-3 space-y-2">
            <div class="flex items-start gap-2">
              <UiIcon name="check" :size="18" class="mt-3 shrink-0 text-[#17663f] dark:text-emerald-300" />
              <LocalizedInput
                v-model="flt.ask"
                class="flex-1 min-w-0"
                :languages="languages"
                :main="def.language"
                :readonly="readonly"
                :aria-label="t('editor.q.filter_n', { n: i + 1 })"
                :placeholder="t('editor.q.filter_placeholder')"
              />
              <button v-if="!readonly" type="button" class="btn-icon btn-ghost" :aria-label="t('editor.q.filter_remove')" @click="field.filters.splice(i, 1)">
                <UiIcon name="close" :size="18" />
              </button>
            </div>
            <details class="text-sm pl-7">
              <summary class="cursor-pointer muted">{{ t('scenarios.filter_criteria') }}</summary>
              <div class="grid gap-3 sm:grid-cols-2 mt-2">
                <div>
                  <label class="label">{{ t('scenarios.filter_yes') }}</label>
                  <LocalizedInput v-model="flt.yes" :languages="languages" :main="def.language" optional :readonly="readonly" :aria-label="t('scenarios.filter_yes')" />
                </div>
                <div>
                  <label class="label">{{ t('scenarios.filter_no') }}</label>
                  <LocalizedInput v-model="flt.no" :languages="languages" :main="def.language" optional :readonly="readonly" :aria-label="t('scenarios.filter_no')" />
                </div>
                <label v-if="ctx?.showKeys.value" class="flex items-center gap-2">
                  {{ t('editor.key') }}
                  <input v-model.trim="flt.key" class="input !h-9 !w-44 code" :readonly="readonly" />
                </label>
              </div>
            </details>
          </div>
        </div>
        <p class="hint">{{ t('editor.q.filters_hint') }}</p>
      </div>

      <!-- a note for the bot -->
      <div v-if="hasDescription || showDescription">
        <label class="label" :for="`${uid}-desc`">{{ t('editor.q.description') }}</label>
        <LocalizedInput v-model="field.description" :input-id="`${uid}-desc`" :languages="languages" :main="def.language" optional :readonly="readonly" />
        <p class="hint">{{ t('editor.q.description_hint') }}</p>
      </div>

      <!-- asked only when the answers say it applies -->
      <ShowIfBlock v-if="field.show_if" :def="def" :show="field.show_if" :languages="languages" :readonly="readonly" @remove="field.show_if = null" />

      <!-- asked only after other questions -->
      <div v-if="afterOptions.length && (field.ask_after.length || showAfter)">
        <p class="label">{{ t('editor.q.after') }}</p>
        <div class="flex flex-wrap gap-1.5">
          <button
            v-for="f in afterOptions"
            :key="f.key"
            type="button"
            class="chip"
            :class="field.ask_after.includes(f.key) ? 'chip-on' : ''"
            :aria-pressed="field.ask_after.includes(f.key)"
            :disabled="readonly"
            @click="toggleIn(field.ask_after, f.key)"
          >
            {{ nameOf(f) }}
          </button>
        </div>
        <p class="hint">{{ t('editor.q.after_hint') }}</p>
        <p v-if="others.some((f: any) => f.show_if)" class="hint">{{ t('editor.show.after_note') }}</p>
      </div>

      <div v-if="ctx?.showKeys.value">
        <label class="label" :for="`${uid}-key`">{{ t('editor.q.key') }}</label>
        <input :id="`${uid}-key`" :value="field.key" class="input code sm:!w-72" :class="keyOk ? '' : '!border-rose-500'" :readonly="readonly" @change="setKey(($event.target as HTMLInputElement).value.trim())" />
        <p class="hint">{{ t('editor.q.key_hint') }}</p>
      </div>

      <div v-if="!readonly" class="flex flex-wrap gap-2 pt-1">
        <button v-if="!isFile" type="button" class="btn-secondary btn-sm" @click="addFilter">{{ t('editor.q.add_filter') }}</button>
        <button v-if="!hasDescription && !showDescription" type="button" class="btn-secondary btn-sm" @click="showDescription = true">{{ t('editor.q.add_description') }}</button>
        <button v-if="afterOptions.length && !field.ask_after.length && !showAfter" type="button" class="btn-secondary btn-sm" @click="showAfter = true">{{ t('editor.q.add_after') }}</button>
        <button
          v-if="!field.show_if"
          type="button"
          class="btn-secondary btn-sm"
          :disabled="dependents.length > 0"
          :title="dependents.length ? t('editor.show.referenced', { names: dependents.join(', ') }) : undefined"
          @click="field.show_if = newShowIf()"
        >
          {{ t('editor.q.add_show_if') }}
        </button>
        <button type="button" class="btn-ghost btn-sm !text-rose-700 dark:!text-rose-300 ml-auto" @click="emit('remove')">
          <UiIcon name="trash" :size="16" /> {{ t('editor.q.remove') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ATTACHMENT_KINDS, ATTACHMENT_TYPES, FIELD_TYPES, KEY_RE, fieldName, newShowIf, renameField, slugKey, type LText } from '~/utils/scenario'
import { pickText } from '~/utils/texts'

const props = defineProps<{
  // the whole scenario: other questions' names, and the places that point at this one
  def: any
  field: any
  index: number
  last: boolean
  open: boolean
  languages: string[]
  readonly?: boolean
  // catalog categories of the tenant, hints for a catalog question
  categories?: string[]
}>()
const emit = defineEmits<{ toggle: []; move: [dir: number]; remove: [] }>()
const { t } = useI18n()
const ctx = useEditorContext()
const uid = useId()

const showFilters = ref(false)
const showDescription = ref(false)
const showAfter = ref(false)

const lang = computed(() => ctx?.lang.value || props.def.language)
const name = computed(() => fieldName(props.field, lang.value, props.def.language))
const questionText = computed(() => pickText(props.field.question, lang.value, props.def.language))
const isFile = computed(() => ATTACHMENT_TYPES.includes(props.field.type))
const keyOk = computed(() => KEY_RE.test(props.field.key || ''))
const hasDescription = computed(() => !!pickText(props.field.description).trim())
const others = computed(() => (props.def.fields as any[]).filter((f) => f.key && f !== props.field))
// a question asked on a condition can not be waited for: if it is never asked, the one
// waiting for it would be stuck for good (the server refuses it too)
const afterOptions = computed(() => others.value.filter((f: any) => !f.show_if || props.field.ask_after.includes(f.key)))
// the questions that wait for this one: while they do, it can not get a condition
const dependents = computed(() => others.value.filter((f: any) => (f.ask_after || []).includes(props.field.key)).map(nameOf))

function nameOf(f: any): string {
  return fieldName(f, lang.value, props.def.language) || t('editor.q.untitled')
}
function fieldsOf(type: string): any[] {
  return others.value.filter((f) => f.type === type)
}

// what answer is expected, in a few words for the closed row
const summary = computed(() => {
  const f = props.field
  const kind = typeSummary(f)
  return f.show_if ? `${kind} · ${t('editor.sum.conditional')}` : kind
})
function typeSummary(f: any): string {
  const base = t(`editor.type_short.${f.type || 'text'}`)
  if (f.type === 'choice' && f.options_from === 'caller') return t('editor.sum.choice_caller')
  if (f.type === 'choice') return t('editor.sum.choice', { n: (f.options || []).length })
  if (f.type === 'catalog') return t('editor.sum.catalog', { n: f.catalog?.max_options || 5 })
  if (f.type === 'date' && f.date?.max_days_ahead !== '' && f.date?.max_days_ahead != null) {
    return t('editor.sum.date', { n: f.date.max_days_ahead })
  }
  if (f.type === 'list') return t('editor.sum.list', { n: f.items?.max_items || 10 })
  return base
}

// a new question takes its key from its name, until somebody types the key by hand
function setTitle(value: LText | null) {
  props.field.title = value
  if (!props.field._auto) return
  const main = pickText(value, props.def.language).trim()
  const taken = others.value.map((f) => f.key)
  setKey(slugKey(main, taken, 'question'), true)
}
function setKey(key: string, auto = false) {
  const from = props.field.key
  props.field.key = key
  if (!auto) props.field._auto = false
  if (KEY_RE.test(key)) renameField(props.def, from, key)
}

// the same for an option / a part of a list item
function setLabel(item: any, list: any[], value: LText | null, fallback: string) {
  item.label = value
  if (!item._auto) return
  const taken = list.filter((x) => x !== item).map((x) => x.key)
  item.key = slugKey(pickText(value, props.def.language).trim(), taken, fallback)
}
function addOption() {
  const taken = (props.field.options as any[]).map((o) => o.key)
  props.field.options.push({ key: slugKey('', taken, 'option'), label: {}, _auto: true })
}
function addPart() {
  const taken = (props.field.items.item_fields as any[]).map((x) => x.key)
  props.field.items.item_fields.push({ key: slugKey('', taken, 'part'), label: {}, required: true, _auto: true })
}
function addFilter() {
  const taken = (props.field.filters as any[]).map((x) => x.key)
  props.field.filters.push({ key: slugKey('', taken, 'check'), ask: {} })
  showFilters.value = true
}

const categoriesText = computed({
  get: () => (props.field.catalog?.categories || []).join(', '),
  set: (v: string) => {
    props.field.catalog ||= { categories: [], max_options: 5 }
    props.field.catalog.categories = v.split(',').map((x) => x.trim()).filter(Boolean)
  },
})
const recipientsText = computed({
  get: () => (props.field.receipt?.recipients || []).join(', '),
  set: (v: string) => {
    props.field.receipt.recipients = v.split(',').map((x) => x.trim()).filter(Boolean)
  },
})

function toggleKind(k: string) {
  const list = props.field.attachment.kinds as string[]
  const i = list.indexOf(k)
  if (i >= 0) {
    if (list.length > 1) list.splice(i, 1)
  } else list.push(k)
}
function toggleIn(list: string[], k: string) {
  const i = list.indexOf(k)
  if (i >= 0) list.splice(i, 1)
  else list.push(k)
}
</script>
