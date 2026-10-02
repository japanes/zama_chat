<template>
  <div class="rounded-card border border-slate-200 dark:border-slate-700">
    <div class="flex flex-wrap items-center gap-2 px-4 py-2.5 border-b border-slate-200 dark:border-slate-700 bg-slate-50/70 dark:bg-slate-900/30 rounded-t-card">
      <span class="text-xs font-semibold muted w-5">{{ index + 1 }}</span>
      <input v-model.trim="field.key" class="input h-8 w-44 code" :class="keyOk ? '' : 'border-rose-400'" :readonly="readonly" :aria-label="t('scenarios.field_key')" />
      <select v-model="field.type" class="select h-8 w-36" :disabled="readonly" :aria-label="t('scenarios.field_type')">
        <option v-for="ft in FIELD_TYPES" :key="ft" :value="ft">{{ t(`scenarios.type.${ft}`) }}</option>
      </select>
      <div v-if="!readonly" class="ml-auto flex gap-1">
        <button type="button" class="btn-icon btn-ghost btn-sm" :disabled="index === 0" :aria-label="t('common.move_up')" @click="emit('move', -1)">
          <UiIcon name="chevron-up" :size="16" />
        </button>
        <button type="button" class="btn-icon btn-ghost btn-sm" :disabled="last" :aria-label="t('common.move_down')" @click="emit('move', 1)">
          <UiIcon name="chevron-down" :size="16" />
        </button>
        <button type="button" class="btn-icon btn-ghost btn-sm text-rose-600" :aria-label="t('common.delete')" @click="emit('remove')">
          <UiIcon name="trash" :size="16" />
        </button>
      </div>
    </div>

    <div class="p-4 space-y-4">
      <div>
        <label class="label">{{ t('scenarios.question') }}</label>
        <LocalizedInput v-model="field.question" :languages="languages" :main="main" multiline :readonly="readonly" />
        <p class="hint">{{ t('scenarios.question_hint') }}</p>
      </div>
      <div>
        <label class="label">{{ t('scenarios.field_description') }} <span class="muted font-normal">({{ t('common.optional') }})</span></label>
        <LocalizedInput v-model="field.description" :languages="languages" :main="main" optional :readonly="readonly" />
        <p class="hint">{{ t('scenarios.field_description_hint') }}</p>
      </div>

      <!-- choice options -->
      <div v-if="field.type === 'choice'">
        <label class="label">{{ t('scenarios.options') }}</label>
        <div class="space-y-2">
          <div v-for="(o, i) in field.options" :key="i" class="flex flex-wrap items-start gap-2">
            <input v-model.trim="o.key" class="input w-36 code" :placeholder="t('scenarios.option_key')" :readonly="readonly" />
            <LocalizedInput v-model="o.label" class="flex-1 min-w-[12rem]" :languages="languages" :main="main" :readonly="readonly" />
            <button v-if="!readonly" type="button" class="btn-icon btn-ghost text-rose-600 mt-6" :aria-label="t('common.delete')" @click="field.options.splice(i, 1)">
              <UiIcon name="trash" :size="16" />
            </button>
          </div>
        </div>
        <button v-if="!readonly" type="button" class="btn-ghost btn-sm mt-2" @click="field.options.push({ key: `option_${field.options.length + 1}`, label: {} })">
          <UiIcon name="plus" :size="14" /> {{ t('scenarios.add_option') }}
        </button>
      </div>

      <!-- catalog item -->
      <div v-if="field.type === 'catalog'" class="grid gap-4 md:grid-cols-[1fr_10rem]">
        <div>
          <label class="label">{{ t('scenarios.catalog_categories') }}</label>
          <input v-model="categoriesText" class="input" :readonly="readonly" :list="`cats-${index}`" />
          <datalist :id="`cats-${index}`">
            <option v-for="c in categories" :key="c" :value="c" />
          </datalist>
          <p class="hint">{{ t('scenarios.catalog_categories_hint') }}</p>
        </div>
        <div>
          <label class="label">{{ t('scenarios.catalog_max_options') }}</label>
          <input v-model.number="field.catalog.max_options" type="number" min="2" max="10" class="input" :readonly="readonly" />
          <p class="hint">{{ t('scenarios.catalog_max_options_hint') }}</p>
        </div>
      </div>

      <!-- date -->
      <div v-if="field.type === 'date'" class="grid gap-4 md:grid-cols-2">
        <label class="flex items-center gap-2 text-sm mt-6">
          <input v-model="field.date.allow_past" type="checkbox" class="checkbox" :disabled="readonly" />
          {{ t('scenarios.date_allow_past') }}
        </label>
        <div>
          <label class="label">{{ t('scenarios.date_max_days') }}</label>
          <input v-model="field.date.max_days_ahead" type="number" min="0" max="3650" class="input" :placeholder="t('scenarios.no_limit')" :readonly="readonly" />
        </div>
      </div>

      <!-- number -->
      <div v-if="field.type === 'number'" class="grid gap-4 md:grid-cols-3">
        <div>
          <label class="label">{{ t('scenarios.number_min') }}</label>
          <input v-model="field.number.min" type="number" class="input" :placeholder="t('scenarios.no_limit')" :readonly="readonly" />
        </div>
        <div>
          <label class="label">{{ t('scenarios.number_max') }}</label>
          <input v-model="field.number.max" type="number" class="input" :placeholder="t('scenarios.no_limit')" :readonly="readonly" />
        </div>
        <label class="flex items-center gap-2 text-sm mt-6">
          <input v-model="field.number.integer" type="checkbox" class="checkbox" :disabled="readonly" />
          {{ t('scenarios.number_integer') }}
        </label>
      </div>

      <!-- list -->
      <div v-if="field.type === 'list'" class="space-y-3">
        <div class="grid gap-4 md:grid-cols-2">
          <div>
            <label class="label">{{ t('scenarios.list_min') }}</label>
            <input v-model.number="field.items.min_items" type="number" min="1" max="50" class="input" :readonly="readonly" />
          </div>
          <div>
            <label class="label">{{ t('scenarios.list_max') }}</label>
            <input v-model.number="field.items.max_items" type="number" min="1" max="50" class="input" :readonly="readonly" />
          </div>
        </div>
        <div>
          <label class="label">{{ t('scenarios.list_item_fields') }}</label>
          <p class="hint -mt-1 mb-2">{{ t('scenarios.list_item_fields_hint') }}</p>
          <div v-for="(x, i) in field.items.item_fields" :key="i" class="flex flex-wrap items-start gap-2 mb-2">
            <input v-model.trim="x.key" class="input w-36 code" :placeholder="t('scenarios.option_key')" :readonly="readonly" />
            <LocalizedInput v-model="x.label" class="flex-1 min-w-[12rem]" :languages="languages" :main="main" :readonly="readonly" />
            <label class="flex items-center gap-1.5 text-sm mt-7">
              <input v-model="x.required" type="checkbox" class="checkbox" :disabled="readonly" />
              {{ t('scenarios.required') }}
            </label>
            <button v-if="!readonly" type="button" class="btn-icon btn-ghost text-rose-600 mt-6" :aria-label="t('common.delete')" @click="field.items.item_fields.splice(i, 1)">
              <UiIcon name="trash" :size="16" />
            </button>
          </div>
          <button v-if="!readonly" type="button" class="btn-ghost btn-sm" @click="field.items.item_fields.push({ key: `part_${field.items.item_fields.length + 1}`, label: {}, required: true })">
            <UiIcon name="plus" :size="14" /> {{ t('scenarios.add_item_field') }}
          </button>
        </div>
      </div>

      <!-- attachment -->
      <div v-if="field.type === 'attachment'" class="grid gap-4 md:grid-cols-[1fr_10rem]">
        <div>
          <label class="label">{{ t('scenarios.attachment_kinds') }}</label>
          <div class="flex flex-wrap gap-3">
            <label v-for="k in ATTACHMENT_KINDS" :key="k" class="flex items-center gap-1.5 text-sm">
              <input type="checkbox" class="checkbox" :checked="field.attachment.kinds.includes(k)" :disabled="readonly" @change="toggleKind(k)" />
              {{ t(`scenarios.attachment_kind.${k}`) }}
            </label>
          </div>
        </div>
        <div>
          <label class="label">{{ t('scenarios.attachment_max') }}</label>
          <input v-model.number="field.attachment.max_items" type="number" min="1" max="10" class="input" :readonly="readonly" />
        </div>
      </div>

      <!-- receipt -->
      <div v-if="field.type === 'receipt'" class="space-y-3">
        <p class="hint">{{ t('scenarios.receipt_hint') }}</p>
        <div class="grid gap-4 md:grid-cols-3">
          <div>
            <label class="label">{{ t('scenarios.receipt_amount') }}</label>
            <input v-model="field.receipt.amount" type="number" min="0" step="0.01" class="input" :readonly="readonly" />
          </div>
          <div>
            <label class="label">{{ t('scenarios.receipt_amount_field') }}</label>
            <select v-model="field.receipt.amount_field" class="select" :disabled="readonly">
              <option value="">—</option>
              <option v-for="k in fieldsOf('catalog')" :key="k" :value="k">{{ k }}</option>
            </select>
          </div>
          <div>
            <label class="label">{{ t('scenarios.receipt_quantity_field') }}</label>
            <select v-model="field.receipt.quantity_field" class="select" :disabled="readonly">
              <option value="">—</option>
              <option v-for="k in fieldsOf('number')" :key="k" :value="k">{{ k }}</option>
            </select>
          </div>
          <div>
            <label class="label">{{ t('scenarios.receipt_currency') }}</label>
            <input v-model.trim="field.receipt.currency" class="input code uppercase" maxlength="3" placeholder="UAH" :readonly="readonly" />
          </div>
          <div>
            <label class="label">{{ t('scenarios.receipt_max_age') }}</label>
            <input v-model.number="field.receipt.max_age_days" type="number" min="0" max="365" class="input" :readonly="readonly" />
          </div>
          <div>
            <label class="label">{{ t('scenarios.receipt_tolerance') }}</label>
            <input v-model="field.receipt.tolerance" type="number" min="0" step="0.01" class="input" :readonly="readonly" />
          </div>
        </div>
        <div>
          <label class="label">{{ t('scenarios.receipt_recipients') }}</label>
          <input v-model="recipientsText" class="input" :readonly="readonly" />
          <p class="hint">{{ t('scenarios.receipt_recipients_hint') }}</p>
        </div>
      </div>

      <!-- filters -->
      <p v-if="isFile" class="hint">{{ t('scenarios.file_no_filters') }}</p>
      <div v-else>
        <label class="label">{{ t('scenarios.filters') }}</label>
        <p class="hint -mt-1 mb-2">{{ t('scenarios.filters_hint') }}</p>
        <div class="space-y-3">
          <div v-for="(flt, i) in field.filters" :key="i" class="rounded-button border border-dashed border-slate-300 dark:border-slate-600 p-3 space-y-2">
            <div class="flex items-center gap-2">
              <input v-model.trim="flt.key" class="input h-8 w-44 code" :placeholder="t('scenarios.filter_key')" :readonly="readonly" />
              <button v-if="!readonly" type="button" class="btn-icon btn-ghost btn-sm text-rose-600 ml-auto" :aria-label="t('common.delete')" @click="field.filters.splice(i, 1)">
                <UiIcon name="trash" :size="16" />
              </button>
            </div>
            <LocalizedInput v-model="flt.ask" :languages="languages" :main="main" multiline :readonly="readonly" :placeholder="t('scenarios.filter_ask')" />
            <details class="text-sm">
              <summary class="cursor-pointer muted">{{ t('scenarios.filter_criteria') }}</summary>
              <div class="grid gap-2 md:grid-cols-2 mt-2">
                <div>
                  <label class="label">{{ t('scenarios.filter_yes') }}</label>
                  <LocalizedInput v-model="flt.yes" :languages="languages" :main="main" optional :readonly="readonly" />
                </div>
                <div>
                  <label class="label">{{ t('scenarios.filter_no') }}</label>
                  <LocalizedInput v-model="flt.no" :languages="languages" :main="main" optional :readonly="readonly" />
                </div>
              </div>
            </details>
          </div>
        </div>
        <button v-if="!readonly" type="button" class="btn-ghost btn-sm mt-2" @click="field.filters.push({ key: `check_${field.filters.length + 1}`, ask: {} })">
          <UiIcon name="plus" :size="14" /> {{ t('scenarios.add_filter') }}
        </button>
      </div>

      <!-- ask_after -->
      <div v-if="others.length">
        <label class="label">{{ t('scenarios.ask_after') }}</label>
        <div class="flex flex-wrap gap-1.5">
          <button
            v-for="k in others"
            :key="k"
            type="button"
            class="btn-sm rounded-badge border code"
            :disabled="readonly"
            :class="field.ask_after.includes(k)
              ? 'border-primary bg-primary/10 text-primary'
              : 'border-slate-200 dark:border-slate-700 text-slate-500'"
            @click="toggleAfter(k)"
          >
            {{ k }}
          </button>
        </div>
        <p class="hint">{{ t('scenarios.ask_after_hint') }}</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ATTACHMENT_KINDS, ATTACHMENT_TYPES, FIELD_TYPES, KEY_RE } from '~/utils/scenario'

const props = defineProps<{
  field: any
  index: number
  last: boolean
  allKeys: string[]
  // all fields of the scenario (receipt: which field gives the price / the quantity)
  allFields?: any[]
  languages: string[]
  main: string
  readonly?: boolean
  // catalog categories of the tenant, for the hints of a catalog field
  categories?: string[]
}>()
const emit = defineEmits<{ move: [dir: number]; remove: [] }>()
const { t } = useI18n()

// categories as "a, b, c"
const categoriesText = computed({
  get: () => (props.field.catalog?.categories || []).join(', '),
  set: (v: string) => {
    props.field.catalog ||= { categories: [], max_options: 5 }
    props.field.catalog.categories = v.split(',').map((x) => x.trim()).filter(Boolean)
  },
})

// recipients as "a, b, c"
const recipientsText = computed({
  get: () => (props.field.receipt?.recipients || []).join(', '),
  set: (v: string) => {
    props.field.receipt.recipients = v.split(',').map((x) => x.trim()).filter(Boolean)
  },
})

const isFile = computed(() => ATTACHMENT_TYPES.includes(props.field.type))
const keyOk = computed(() => KEY_RE.test(props.field.key || ''))

function fieldsOf(type: string): string[] {
  return (props.allFields || []).filter((f) => f.type === type && f.key && f.key !== props.field.key).map((f) => f.key)
}

function toggleKind(k: string) {
  const list = props.field.attachment.kinds as string[]
  const i = list.indexOf(k)
  if (i >= 0) {
    if (list.length > 1) list.splice(i, 1)
  } else list.push(k)
}
const others = computed(() => props.allKeys.filter((k) => k && k !== props.field.key))

function toggleAfter(k: string) {
  const list = props.field.ask_after as string[]
  const i = list.indexOf(k)
  if (i >= 0) list.splice(i, 1)
  else list.push(k)
}
</script>
