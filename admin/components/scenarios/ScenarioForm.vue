<template>
  <!-- the form editor; edits `def` in place (the page keeps the YAML / JSON tabs in sync) -->
  <div class="space-y-5">
    <!-- basics -->
    <section class="card card-body space-y-4">
      <h2 class="card-title">{{ t('scenarios.section.basics') }}</h2>
      <div class="grid gap-4 md:grid-cols-3">
        <div>
          <label class="label">{{ t('scenarios.key') }}</label>
          <input v-model.trim="def.key" class="input code" :class="keyOk ? '' : 'border-rose-400'" :readonly="readonly || !isNew" />
          <p class="hint">{{ isNew ? t('scenarios.key_hint') : t('scenarios.key_fixed') }}</p>
        </div>
        <div>
          <label class="label">{{ t('scenarios.main_language') }}</label>
          <select v-model="def.language" class="select" :disabled="readonly">
            <option v-for="l in languageChoices" :key="l" :value="l">{{ l }}</option>
          </select>
          <p class="hint">{{ t('scenarios.main_language_hint') }}</p>
        </div>
        <div>
          <label class="label">{{ t('scenarios.completion') }}</label>
          <select v-model="def.completion" class="select" :disabled="readonly">
            <option v-for="c in COMPLETIONS" :key="c" :value="c">{{ t(`scenarios.completion_mode.${c}`) }}</option>
          </select>
        </div>
      </div>
      <div>
        <label class="label">{{ t('scenarios.title') }}</label>
        <LocalizedInput v-model="def.title" :languages="languages" :main="def.language" :readonly="readonly" />
      </div>
      <div>
        <label class="label">{{ t('scenarios.description') }} <span class="muted font-normal">({{ t('common.optional') }})</span></label>
        <LocalizedInput v-model="def.description" :languages="languages" :main="def.language" multiline optional :readonly="readonly" />
        <p class="hint">{{ t('scenarios.description_hint') }}</p>
      </div>
    </section>

    <!-- start -->
    <section class="card card-body space-y-4">
      <div>
        <h2 class="card-title">{{ t('scenarios.section.start') }}</h2>
        <p class="page-sub">{{ t('scenarios.start_hint') }}</p>
      </div>
      <div class="space-y-3">
        <div v-for="(q, i) in def.start.questions" :key="i" class="rounded-button border border-slate-200 dark:border-slate-700 p-3 space-y-2">
          <div class="flex flex-wrap items-center gap-2">
            <select :value="q.field !== undefined ? 'field' : 'own'" class="select h-8 w-48" :disabled="readonly" @change="setKind(q, ($event.target as HTMLSelectElement).value)">
              <option value="own">{{ t('scenarios.start_own') }}</option>
              <option value="field">{{ t('scenarios.start_field') }}</option>
            </select>
            <input v-if="q.field === undefined" v-model.trim="q.key" class="input h-8 w-44 code" :placeholder="t('scenarios.start_key')" :readonly="readonly" />
            <select v-else v-model="q.field" class="select h-8 w-44" :disabled="readonly">
              <option v-for="k in fieldKeys" :key="k" :value="k">{{ k }}</option>
            </select>
            <label class="flex items-center gap-1.5 text-sm">
              {{ t('scenarios.weight') }}
              <input v-model.number="q.weight" type="number" min="1" max="100" class="input h-8 w-20" :readonly="readonly" />
            </label>
            <button v-if="!readonly" type="button" class="btn-icon btn-ghost btn-sm text-rose-600 ml-auto" :disabled="def.start.questions.length < 2" :aria-label="t('common.delete')" @click="def.start.questions.splice(i, 1)">
              <UiIcon name="trash" :size="16" />
            </button>
          </div>
          <LocalizedInput v-if="q.field === undefined" v-model="q.ask" :languages="languages" :main="def.language" multiline :readonly="readonly" :placeholder="t('scenarios.start_ask')" />
          <p v-else class="text-xs muted">{{ t('scenarios.start_field_hint', { field: q.field || '…' }) }}</p>
        </div>
      </div>
      <div class="flex flex-wrap items-end gap-4">
        <button v-if="!readonly" type="button" class="btn-ghost btn-sm" @click="def.start.questions.push({ key: `sign_${def.start.questions.length + 1}`, ask: {}, weight: 1 })">
          <UiIcon name="plus" :size="14" /> {{ t('scenarios.add_start') }}
        </button>
        <label class="flex items-center gap-2 text-sm ml-auto">
          {{ t('scenarios.threshold') }}
          <input v-model.number="def.start.threshold" type="number" min="1" class="input h-8 w-20" :readonly="readonly" />
          <span class="muted">/ {{ total }}</span>
        </label>
      </div>
      <p v-if="threshold > total" class="alert-red">{{ t('scenarios.threshold_over', { threshold, total }) }}</p>
      <div v-else-if="examples.length" class="alert-blue">
        <p class="font-medium">{{ t('scenarios.starts_when') }}</p>
        <ul class="mt-1 space-y-0.5">
          <li v-for="(ex, i) in examples" :key="i" class="code text-xs">{{ ex.join(' + ') }}</li>
        </ul>
      </div>
    </section>

    <!-- fields -->
    <section class="space-y-3">
      <div class="flex items-end justify-between gap-3">
        <div>
          <h2 class="card-title">{{ t('scenarios.section.fields') }}</h2>
          <p class="page-sub">{{ t('scenarios.fields_hint') }}</p>
        </div>
        <button v-if="!readonly" type="button" class="btn-secondary btn-sm" @click="addField">
          <UiIcon name="plus" :size="14" /> {{ t('scenarios.add_field') }}
        </button>
      </div>
      <FieldCard
        v-for="(f, i) in (def.fields as any[])"
        :key="i"
        :field="f"
        :index="i"
        :last="i === def.fields.length - 1"
        :all-keys="fieldKeys"
        :languages="languages"
        :main="def.language"
        :readonly="readonly"
        @move="moveField(i, $event)"
        @remove="removeField(i)"
      />
    </section>

    <!-- resume / cancel -->
    <section class="card card-body space-y-4">
      <h2 class="card-title">{{ t('scenarios.section.flow') }}</h2>
      <div class="grid gap-4 md:grid-cols-3">
        <div>
          <label class="label">{{ t('scenarios.resume') }}</label>
          <select v-model="def.resume.mode" class="select" :disabled="readonly">
            <option value="always">{{ t('scenarios.resume_mode.always') }}</option>
            <option value="within_hours">{{ t('scenarios.resume_mode.within_hours') }}</option>
          </select>
        </div>
        <div v-if="def.resume.mode === 'within_hours'">
          <label class="label">{{ t('scenarios.resume_hours') }}</label>
          <input v-model.number="def.resume.hours" type="number" min="0.1" step="0.5" class="input" :readonly="readonly" />
        </div>
      </div>
      <p class="hint -mt-2">{{ t('scenarios.resume_hint') }}</p>
      <div>
        <label class="label">{{ t('scenarios.cancel') }} <span class="muted font-normal">({{ t('common.optional') }})</span></label>
        <LocalizedInput v-model="def.cancel.description" :languages="languages" :main="def.language" multiline optional :readonly="readonly" />
        <p class="hint">{{ t('scenarios.cancel_hint') }}</p>
      </div>
    </section>

    <!-- actions -->
    <section class="card card-body space-y-3">
      <div>
        <h2 class="card-title">{{ t('scenarios.section.actions') }}</h2>
        <p class="page-sub">{{ t('scenarios.actions_hint') }}</p>
      </div>
      <div v-for="(a, i) in def.actions" :key="i" class="flex flex-wrap items-end gap-2 rounded-button border border-slate-200 dark:border-slate-700 p-3">
        <div class="w-40">
          <label class="label">{{ t('scenarios.action_on') }}</label>
          <select v-model="a.on" class="select" :disabled="readonly">
            <option v-for="e in ACTION_EVENTS" :key="e" :value="e">{{ t(`scenarios.event.${e}`) }}</option>
          </select>
        </div>
        <div class="w-36">
          <label class="label">{{ t('scenarios.action_do') }}</label>
          <select v-model="a.do" class="select" :disabled="readonly">
            <option v-for="d in ACTIONS" :key="d" :value="d">{{ t(`scenarios.action.${d}`) }}</option>
          </select>
        </div>
        <div class="w-44">
          <label class="label">{{ t('scenarios.endpoint') }}</label>
          <select v-if="endpoints.length" v-model="a.endpoint" class="select" :disabled="readonly">
            <option value="">—</option>
            <option v-for="e in endpointChoices(a.endpoint)" :key="e" :value="e">{{ e }}</option>
          </select>
          <input v-else v-model.trim="a.endpoint" class="input code" :readonly="readonly" />
        </div>
        <div v-if="a.on === 'silence'" class="w-28">
          <label class="label">{{ t('scenarios.after_minutes') }}</label>
          <input v-model.number="a.after_minutes" type="number" min="1" class="input" :placeholder="t('scenarios.default')" :readonly="readonly" />
        </div>
        <div v-if="a.on === 'field_filled' || a.on === 'field_stuck'" class="w-40">
          <label class="label">{{ t('scenarios.action_field') }}</label>
          <select v-model="a.field" class="select" :disabled="readonly">
            <option :value="undefined">{{ t('scenarios.any_field') }}</option>
            <option v-for="k in fieldKeys" :key="k" :value="k">{{ k }}</option>
          </select>
        </div>
        <div class="w-32">
          <label class="label">{{ t('scenarios.action_status') }}</label>
          <input v-model.trim="a.status" class="input code" :placeholder="t('scenarios.default')" :readonly="readonly" />
        </div>
        <button v-if="!readonly" type="button" class="btn-icon btn-ghost text-rose-600 ml-auto" :aria-label="t('common.delete')" @click="def.actions.splice(i, 1)">
          <UiIcon name="trash" :size="16" />
        </button>
      </div>
      <button v-if="!readonly" type="button" class="btn-ghost btn-sm" @click="def.actions.push({ on: 'completed', do: 'http_post', endpoint: endpoints[0] || '' })">
        <UiIcon name="plus" :size="14" /> {{ t('scenarios.add_action') }}
      </button>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ACTIONS, ACTION_EVENTS, COMPLETIONS, KEY_RE, newField, startExamples, startTotal } from '~/utils/scenario'

const props = defineProps<{
  def: any
  languages: string[]
  endpoints: string[]
  isNew?: boolean
  readonly?: boolean
}>()
const { t } = useI18n()

const keyOk = computed(() => KEY_RE.test(props.def.key || ''))
const fieldKeys = computed(() => (props.def.fields as any[]).map((f) => f.key).filter(Boolean))
const languageChoices = computed(() => {
  const out = [...props.languages]
  if (props.def.language && !out.includes(props.def.language)) out.push(props.def.language)
  return out
})
const total = computed(() => startTotal(props.def.start))
const threshold = computed(() =>
  props.def.start.threshold === '' || props.def.start.threshold == null
    ? Number(props.def.start.questions[0]?.weight) || 1
    : Number(props.def.start.threshold),
)
const examples = computed(() => startExamples(props.def.start))

function setKind(q: any, kind: string) {
  if (kind === 'field') {
    delete q.key
    delete q.ask
    q.field = fieldKeys.value[0] || ''
  } else {
    delete q.field
    q.key = 'sign'
    q.ask = {}
  }
}

function addField() {
  props.def.fields.push(newField(props.def.fields.length + 1))
}
function moveField(i: number, dir: number) {
  const list = props.def.fields as any[]
  const j = i + dir
  if (j < 0 || j >= list.length) return
  ;[list[i], list[j]] = [list[j], list[i]]
}
function removeField(i: number) {
  const [gone] = props.def.fields.splice(i, 1)
  // references to the removed field go with it
  for (const f of props.def.fields) f.ask_after = (f.ask_after || []).filter((k: string) => k !== gone.key)
}
function endpointChoices(current?: string) {
  return current && !props.endpoints.includes(current) ? [...props.endpoints, current] : props.endpoints
}
</script>
