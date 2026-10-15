<template>
  <div class="max-w-[1180px] mx-auto space-y-5">
    <!-- The scenario editor: five steps in the order a conversation goes, an example of
         the conversation next to them, YAML / JSON / keys for developers at the bottom. -->
    <UiState :loading="loading" :error="error" :has-data="ready" :retry="load">
      <template v-if="ready">
        <header class="flex flex-wrap items-end justify-between gap-4">
          <div class="min-w-0 space-y-1.5">
            <NuxtLink :to="`${base}/scenarios`" class="link inline-flex items-center gap-1.5 text-sm">
              <UiIcon name="chevron-left" :size="16" /> {{ t('editor.all') }}
            </NuxtLink>
            <h1 class="text-[28px] leading-tight font-bold text-slate-900 dark:text-white break-words">{{ titleText || t('editor.untitled') }}</h1>
            <div class="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-sm text-slate-600 dark:text-slate-300">
              <span v-if="isNew" class="badge-gray">{{ t('editor.status.new') }}</span>
              <template v-else>
                <span v-if="activeVersion" class="badge-green">{{ t('editor.status.on') }}</span>
                <span v-else class="badge-gray">{{ t('editor.status.off') }}</span>
                <span v-if="shown">{{ t('editor.status.version', { v: shown.version, date: f.dateTime(shown.created_at) }) }}</span>
                <span v-if="viewing && activeVersion && viewing !== activeVersion" class="badge-amber">{{ t('editor.status.viewing', { v: activeVersion }) }}</span>
                <button type="button" class="link" @click="versionsOpen = true">{{ t('editor.status.history') }}</button>
              </template>
            </div>
            <p v-if="!isNew && activeVersion && !inChannels.length" class="text-sm text-[#7a4b00] dark:text-amber-300">
              {{ t('editor.status.no_channels') }}
              <NuxtLink v-if="canEdit" :to="`${base}/channels`" class="link">{{ t('nav.channels') }}</NuxtLink>
            </p>
          </div>
          <div class="flex flex-wrap gap-2.5">
            <NuxtLink v-if="!isNew" :to="`${base}/sandbox`" class="btn-secondary">{{ t('editor.try') }}</NuxtLink>
            <button type="button" class="btn-secondary" :disabled="busy" @click="check">{{ t('scenarios.check') }}</button>
            <button v-if="canEdit && !isNew && !activeVersion" type="button" class="btn-secondary" :disabled="busy" @click="activate(viewing!)">{{ t('editor.turn_on') }}</button>
            <button v-if="canEdit" type="button" class="btn-primary !px-6" :disabled="busy" @click="askSave">{{ t('common.save') }}</button>
          </div>
        </header>

        <!-- the server's verdict: an error stops saving, warnings are only shown -->
        <div v-if="problem" class="alert-red" role="alert">{{ problem }}</div>
        <div v-if="checkedOk && !problem" class="alert-green" role="status">{{ t('scenarios.check_ok') }}</div>
        <details v-if="warnings.length" class="alert-amber">
          <summary class="cursor-pointer font-medium">{{ t('editor.warnings', { n: warnings.length }) }}</summary>
          <ul class="mt-2 list-disc pl-5 space-y-0.5 text-sm">
            <li v-for="(w, i) in warnings" :key="i">{{ codeText(w) }}</li>
          </ul>
        </details>

        <div v-if="langs.length > 1 && mode === 'form'" class="flex flex-wrap items-center gap-x-4 gap-y-2.5">
          <span class="text-sm font-semibold text-slate-600 dark:text-slate-300">{{ t('editor.lang.title') }}</span>
          <div class="segmented">
            <button
              v-for="l in langs"
              :key="l"
              type="button"
              class="segment"
              :class="lang === l ? 'segment-active' : ''"
              :aria-pressed="lang === l"
              @click="lang = l"
            >
              {{ languageName(l, locale) }}<template v-if="l === def.language"> · {{ t('editor.lang.main') }}</template>
            </button>
          </div>
          <span class="flex-1 basis-[280px] text-sm muted">{{ t('editor.lang.hint') }}</span>
        </div>

        <div v-if="mode === 'form'" class="flex flex-wrap items-start gap-7">
          <div class="flex-[999_1_520px] min-w-0 space-y-3.5">
            <section class="card card-body grid gap-4 sm:grid-cols-2">
              <div>
                <label class="label" :for="`${uid}-title`">{{ t('editor.name') }}</label>
                <LocalizedInput
                  :model-value="def.title"
                  :input-id="`${uid}-title`"
                  :languages="languages"
                  :main="def.language"
                  :readonly="!canEdit"
                  :placeholder="t('editor.name_placeholder')"
                  @update:model-value="setTitle"
                />
              </div>
              <div>
                <label class="label" :for="`${uid}-about`">{{ t('editor.about') }} <span class="muted font-normal">({{ t('common.optional') }})</span></label>
                <LocalizedInput v-model="def.description" :input-id="`${uid}-about`" :languages="languages" :main="def.language" optional :readonly="!canEdit" />
                <p class="hint">{{ t('editor.about_hint') }}</p>
              </div>
            </section>

            <EditorStep :n="1" :title="t('editor.step.start')" :summary="summary.start" :open="open[1]" :problems="problems[1]" @toggle="open[1] = !open[1]">
              <StepStart :def="def" :languages="languages" :readonly="!canEdit" />
            </EditorStep>
            <EditorStep :n="2" :title="t('editor.step.questions')" :summary="summary.questions" :open="open[2]" :problems="problems[2]" @toggle="open[2] = !open[2]">
              <StepQuestions :def="def" :languages="languages" :readonly="!canEdit" :categories="categories" :history-default="defaults.history_messages" />
            </EditorStep>
            <EditorStep :n="3" :title="t('editor.step.confirm')" :summary="summary.confirm" :open="open[3]" @toggle="open[3] = !open[3]">
              <StepConfirm :def="def" :languages="languages" :readonly="!canEdit" />
            </EditorStep>
            <EditorStep :n="4" :title="t('editor.step.next')" :summary="summary.next" :open="open[4]" :problems="problems[4]" @toggle="open[4] = !open[4]">
              <StepNext :def="def" :languages="languages" :endpoints="endpoints" :notifiers="notifiers" :readonly="!canEdit" />
            </EditorStep>
            <EditorStep :n="5" :title="t('editor.step.leave')" :summary="summary.leave" :open="open[5]" :problems="problems[5]" @toggle="open[5] = !open[5]">
              <StepLeave :def="def" :languages="languages" :scenarios="scenarios" :readonly="!canEdit" />
            </EditorStep>

            <div class="flex flex-wrap items-center gap-x-4 gap-y-1 px-1 text-sm muted">
              <span>{{ t('editor.dev.title') }}:</span>
              <button type="button" class="link" @click="switchMode('yaml')">{{ t('editor.dev.yaml') }}</button>
              <button type="button" class="link" @click="switchMode('json')">JSON</button>
              <button type="button" class="link" @click="downloadFile">{{ t('editor.dev.download') }}</button>
              <NuxtLink to="/docs/scenario-file" class="link">{{ t('editor.dev.format') }}</NuxtLink>
              <button type="button" class="link" :aria-expanded="showKeys" @click="showKeys = !showKeys">
                {{ t(showKeys ? 'editor.dev.keys_hide' : 'editor.dev.keys') }}
              </button>
            </div>
            <section v-if="showKeys" class="card card-body grid gap-4 sm:grid-cols-2">
              <div>
                <label class="label" :for="`${uid}-key`">{{ t('editor.dev.key') }}</label>
                <input
                  :id="`${uid}-key`"
                  :value="def.key"
                  class="input code"
                  :class="keyOk ? '' : '!border-rose-500'"
                  :readonly="!canEdit || !isNew"
                  @input="setKey(($event.target as HTMLInputElement).value.trim())"
                />
                <p class="hint">{{ isNew ? t('scenarios.key_hint') : t('scenarios.key_fixed') }}</p>
              </div>
              <div>
                <label class="label" :for="`${uid}-main`">{{ t('scenarios.main_language') }}</label>
                <select :id="`${uid}-main`" v-model="def.language" class="select" :disabled="!canEdit">
                  <option v-for="l in langs" :key="l" :value="l">{{ languageName(l, locale) }} ({{ l }})</option>
                </select>
                <p class="hint">{{ t('scenarios.main_language_hint') }}</p>
              </div>
            </section>
          </div>

          <div class="flex-[1_1_320px] min-w-0 xl:max-w-[380px] xl:sticky xl:top-20 xl:max-h-[calc(100dvh-6rem)] xl:overflow-y-auto xl:rounded-card">
            <ScenarioPreview :def="def" :sandbox="isNew ? null : `${base}/sandbox`" />
          </div>
        </div>

        <!-- the same scenario as text -->
        <section v-else class="card card-body space-y-3">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <h2 class="card-title">{{ t(mode === 'yaml' ? 'editor.dev.as_yaml' : 'editor.dev.as_json') }}</h2>
            <button type="button" class="btn-secondary btn-sm" @click="switchMode('form')">{{ t('editor.dev.back') }}</button>
          </div>
          <p class="hint !mt-0">{{ t('editor.dev.text_hint') }}</p>
          <textarea
            v-model="source"
            class="textarea code text-xs leading-5 min-h-[32rem]"
            spellcheck="false"
            :aria-label="t(mode === 'yaml' ? 'editor.dev.as_yaml' : 'editor.dev.as_json')"
            :readonly="!canEdit"
          />
          <p v-if="parseError" class="alert-red" role="alert">{{ parseError }}</p>
        </section>
      </template>
    </UiState>

    <!-- versions -->
    <UiModal :open="versionsOpen" :title="t('editor.status.history')" @close="versionsOpen = false">
      <ul class="space-y-2">
        <li v-for="v in versions" :key="v.version" class="rounded-button border border-slate-200 dark:border-slate-700 p-3 text-sm">
          <div class="flex flex-wrap items-center gap-2">
            <span class="font-semibold text-slate-900 dark:text-white">{{ t('editor.versions.n', { v: v.version }) }}</span>
            <span v-if="v.is_active" class="badge-green">{{ t('editor.versions.active') }}</span>
            <span v-if="v.version === viewing" class="badge-gray">{{ t('editor.versions.shown') }}</span>
            <span class="ml-auto muted">{{ f.dateTime(v.created_at) }}</span>
          </div>
          <p class="mt-0.5 muted truncate">{{ v.author || '—' }}</p>
          <p v-if="v.note" class="mt-1 whitespace-pre-line">{{ v.note }}</p>
          <div class="mt-2 flex flex-wrap gap-2">
            <button v-if="v.version !== viewing" type="button" class="btn-secondary btn-sm" :disabled="busy" @click="openVersion(v.version)">{{ t('scenarios.open') }}</button>
            <button v-if="canEdit && !v.is_active" type="button" class="btn-secondary btn-sm" :disabled="busy" @click="activate(v.version)">
              <UiIcon name="rollback" :size="14" /> {{ t('editor.versions.activate') }}
            </button>
          </div>
        </li>
      </ul>
      <template v-if="canEdit && activeVersion" #footer>
        <button type="button" class="btn-danger" :disabled="busy" @click="disable">{{ t('editor.versions.disable') }}</button>
      </template>
    </UiModal>

    <!-- save -->
    <UiModal :open="saveOpen" :title="t('scenarios.save_title')" @close="saveOpen = false">
      <div class="space-y-4">
        <ul v-if="allProblems.length" class="alert-amber space-y-1">
          <li class="font-medium">{{ t('editor.save.problems') }}</li>
          <li v-for="(p, i) in allProblems" :key="i">{{ p }}</li>
        </ul>
        <div>
          <label class="label" :for="`${uid}-note`">{{ t('scenarios.note') }} <span class="muted font-normal">({{ t('common.optional') }})</span></label>
          <textarea :id="`${uid}-note`" v-model="note" rows="2" class="textarea" :placeholder="t('scenarios.note_placeholder')" />
        </div>
        <label class="flex items-center gap-2.5 min-h-[44px]">
          <input v-model="activateOnSave" type="checkbox" class="checkbox" />
          {{ t('editor.save.activate') }}
        </label>
        <div v-if="isNew && channels.length">
          <p class="label">{{ t('editor.save.channels') }}</p>
          <label v-for="c in channels" :key="c.id" class="flex items-center gap-2.5 min-h-[40px]">
            <input v-model="enableIn" type="checkbox" class="checkbox" :value="c.id" />
            {{ c.title }}
          </label>
          <p class="hint">{{ t('editor.save.channels_hint') }}</p>
        </div>
      </div>
      <template #footer>
        <button type="button" class="btn-secondary" @click="saveOpen = false">{{ t('common.cancel') }}</button>
        <button type="button" class="btn-primary" :disabled="busy" @click="save">{{ t('common.save') }}</button>
      </template>
    </UiModal>
  </div>
</template>

<script setup lang="ts">
import yaml from 'js-yaml'
import { EDITOR_CONTEXT, languageName } from '~/composables/useEditorContext'
import { downloadText } from '~/utils/download'
import {
  KEY_RE,
  NOTIFY_ACTIONS,
  cleanDefinition,
  fieldName,
  newDefinition,
  signLabel,
  slugKey,
  startSets,
  startThreshold,
  startTotal,
  toForm,
  type LText,
} from '~/utils/scenario'
import { pickText } from '~/utils/texts'

type Mode = 'form' | 'yaml' | 'json'

const props = defineProps<{
  // the scenario to open; null = a new one
  scenarioKey: string | null
  // a new scenario starts from this definition (a template, a draft) instead of a blank
  draft?: any | null
}>()

const { t, locale } = useI18n()
const api = useApi()
const f = useFormat()
const toast = useToast()
const confirm = useConfirm()
const uid = useId()
const { errorText, codeText } = useErrorText()
const { base, path, can } = useTenant()
const { channels, load: loadInfo } = useTenantInfo()

const isNew = computed(() => !props.scenarioKey)
const canEdit = computed(() => can('admin'))

const def = ref<any>(null)
const languages = ref<string[]>([])
const versions = ref<any[]>([])
const endpoints = ref<string[]>([])
const notifiers = ref<{ name: string; kind: string }[]>([])
const categories = ref<string[]>([])
// the tenant's scenarios: [{key, title, channels}]
const allScenarios = ref<any[]>([])
// what the server gives a scenario that leaves a setting out
const defaults = ref<{ history_messages?: number }>({})
const viewing = ref<number | null>(null)
const mode = ref<Mode>('form')
const source = ref('')
const parseError = ref<string | null>(null)
const problem = ref<string | null>(null)
const warnings = ref<any[]>([])
const checkedOk = ref(false)
const busy = ref(false)
const saveOpen = ref(false)
const versionsOpen = ref(false)
const note = ref('')
const activateOnSave = ref(true)
const enableIn = ref<string[]>([])
// a new scenario takes its key from its name until somebody types the key by hand
const autoKey = ref(true)
// what is saved on the server, to warn before leaving with changes
const saved = ref('')
const open = reactive<Record<number, boolean>>({ 1: false, 2: true, 3: false, 4: false, 5: false })
// a blank scenario is not scolded for being blank: the list of what is missing shows
// after the first «Check» or «Save»
const strict = ref(true)

const lang = ref('')
const showKeys = ref(false)
provide(EDITOR_CONTEXT, { lang, showKeys })

const ready = computed(() => !!def.value)
const activeVersion = computed<number | null>(() => versions.value.find((v) => v.is_active)?.version || null)
const shown = computed(() => versions.value.find((v) => v.version === viewing.value) || null)
const keyOk = computed(() => KEY_RE.test(def.value?.key || ''))
const titleText = computed(() => pickText(def.value?.title, lang.value, def.value?.language))
const scenarios = computed(() => allScenarios.value.map((s) => ({ key: s.key, title: s.title })))
const inChannels = computed<any[]>(() =>
  (allScenarios.value.find((s) => s.key === props.scenarioKey)?.channels || []).filter((c: any) => c.is_enabled),
)
// the tenant's languages, the main one first, then the ones the texts already have
const langs = computed(() => {
  const out = [...languages.value]
  const main = def.value?.language
  if (main && !out.includes(main)) out.unshift(main)
  return out
})

usePage(() => [
  { label: t('nav.scenarios'), to: `${base.value}/scenarios` },
  { label: isNew.value ? titleText.value || t('scenarios.new') : titleText.value || props.scenarioKey || '' },
])

function snapshot(): string {
  return def.value ? JSON.stringify(cleanDefinition(def.value)) : ''
}

const { loading, error, load } = useLoader(async () => {
  const info = await loadInfo()
  languages.value = info?.effective_settings?.languages || []
  if (isNew.value) {
    const main = languages.value[0] || 'en'
    def.value = toForm(props.draft || newDefinition(main))
    versions.value = []
    viewing.value = null
    autoKey.value = !props.draft?.key
    strict.value = !!props.draft
    // a new scenario works where the bot works, unless the person unticks a channel
    enableIn.value = channels.value.map((c: any) => c.id)
    for (const n of [1, 2]) open[n] = true
  } else {
    const res = await api<any>(path(`/scenarios/${props.scenarioKey}`))
    languages.value = res.languages
    versions.value = res.versions
    def.value = toForm(res.current.definition)
    viewing.value = res.current.version
    warnings.value = res.warnings
  }
  if (!langs.value.includes(lang.value)) lang.value = def.value.language
  mode.value = 'form'
  // a draft (a template, a description) is not saved anywhere yet
  saved.value = isNew.value && props.draft ? '' : snapshot()
  return def.value
})

// --- what the steps say when closed -------------------------------------------------

function fname(x: any): string {
  return fieldName(x, lang.value, def.value.language) || t('editor.q.untitled')
}

const summary = computed(() => {
  const d = def.value
  const sets = startSets(d.start, 2)
  const start = sets.length
    ? t('editor.sum.start', {
        signs: sets.map((set) => set.map((i) => signLabel(d, i, lang.value, t)).join(` ${t('editor.start.and')} `)).join(` — ${t('editor.sum.or')} `),
      })
    : t('editor.sum.start_never')
  const confirmOn = !!d.confirm || d.completion === 'explicit_yes'
  const hours = d.waiting?.hours
  const next =
    d.completion === 'external_event'
      ? hours
        ? t('editor.sum.next_event_hours', { n: hours })
        : t('editor.sum.next_event')
      : d.completion === 'receipt'
        ? t('editor.sum.next_receipt')
        : d.completion === 'by_caller'
          ? t('editor.sum.next_caller')
          : t('editor.sum.next_all')
  return {
    start,
    questions: t('editor.sum.questions', { n: d.fields.length }),
    confirm: t(confirmOn ? 'editor.sum.confirm_on' : 'editor.sum.confirm_off'),
    next: `${next} ${t('editor.sum.actions', { n: d.actions.length })}`,
    leave: d.resume?.mode === 'within_hours' ? t('editor.sum.leave_hours', { n: d.resume.hours || '…' }) : t('editor.sum.leave_always'),
  }
})

// --- what is not filled in yet (the server checks the same when saving) --------------

const problems = computed<Record<number, string[]>>(() => (strict.value ? found.value : { 1: [], 2: [], 4: [], 5: [] }))
const found = computed<Record<number, string[]>>(() => {
  const d = def.value
  const out: Record<number, string[]> = { 1: [], 2: [], 4: [], 5: [] }
  const main = d.language
  const has = (value: unknown) => !!pickText(value, main).trim()

  if (startThreshold(d.start) > startTotal(d.start)) out[1].push(t('editor.problem.never'))
  ;(d.start.questions as any[]).forEach((q, i) => {
    if (q.field === undefined && !has(q.ask)) out[1].push(t('editor.problem.sign_empty', { n: i + 1 }))
  })

  if (!has(d.title)) out[2].push(t('editor.problem.no_name'))
  else if (!KEY_RE.test(d.key || '')) out[2].push(t('editor.problem.bad_scenario_key'))
  else if (isNew.value && allScenarios.value.some((x) => x.key === d.key)) out[2].push(t('editor.problem.key_taken', { key: d.key }))
  ;(d.fields as any[]).forEach((x, i) => {
    const name = fname(x)
    if (!KEY_RE.test(x.key || '')) out[2].push(t('editor.problem.bad_key', { name }))
    if (!has(x.question)) out[2].push(t('editor.problem.no_question', { name }))
    if (x.type === 'choice' && x.options_from !== 'caller') {
      if ((x.options || []).length < 2) out[2].push(t('editor.problem.few_options', { name }))
      else if ((x.options as any[]).some((o) => !has(o.label))) out[2].push(t('editor.problem.option_empty', { name }))
    }
    if ((x.filters as any[]).some((flt) => !has(flt.ask))) out[2].push(t('editor.problem.filter_empty', { name }))
    if (x.show_if) {
      ;(x.show_if.questions as any[]).forEach((q, n) => {
        if (!has(q.ask)) out[2].push(t('editor.problem.show_empty', { name, n: n + 1 }))
      })
      if (startThreshold(x.show_if) > startTotal(x.show_if)) out[2].push(t('editor.problem.show_never', { name }))
    }
    for (const ref of x.ask_after || []) {
      const other = (d.fields as any[]).find((f) => f.key === ref)
      if (other?.show_if) out[2].push(t('editor.problem.after_conditional', { name, ref: fname(other) }))
    }
  })

  if (d.completion === 'external_event') {
    if (!d.events_list.length) out[4].push(t('editor.problem.no_events'))
    for (const e of d.events_list as any[]) {
      if (!KEY_RE.test(e.name || '')) out[4].push(t('editor.problem.event_code'))
      else if (e.result === 'reopen' && !e.reopen_fields.length) out[4].push(t('editor.problem.event_reopen', { name: e.name }))
    }
  }
  if (d.completion === 'receipt' && !(d.fields as any[]).some((x) => x.type === 'receipt')) out[4].push(t('editor.problem.no_receipt'))
  for (const a of d.actions as any[]) {
    const what = t(`editor.next.do.${a.do}`)
    if (a.do === 'http_post' && !a.endpoint) out[4].push(t('editor.problem.no_target', { what }))
    if (NOTIFY_ACTIONS.includes(a.do) && !a.params?.notifier) out[4].push(t('editor.problem.no_target', { what }))
    if (a.do === 'message_client' && !has(a.params?.text)) out[4].push(t('editor.problem.no_text', { what }))
  }

  if (d.resume?.mode === 'within_hours' && !(Number(d.resume.hours) > 0)) out[5].push(t('editor.problem.resume_hours'))
  return out
})
const allProblems = computed(() => (mode.value === 'form' ? Object.values(found.value).flat() : []))

// --- name → key ---------------------------------------------------------------------

function setTitle(value: LText | null) {
  def.value.title = value ?? {}
  if (!isNew.value || !autoKey.value) return
  const taken = allScenarios.value.map((s) => s.key)
  def.value.key = slugKey(pickText(value, def.value.language).trim(), taken, 'scenario')
}
function setKey(key: string) {
  def.value.key = key
  autoKey.value = false
}

// --- form ⇄ text: the text is parsed only when leaving it ----------------------------

function asYaml(): string {
  return yaml.dump(cleanDefinition(def.value), { lineWidth: 100, noRefs: true })
}

function switchMode(next: Mode) {
  if (next === mode.value) return
  if (mode.value !== 'form') {
    const parsed = parseSource()
    if (!parsed) return
    def.value = toForm(parsed)
    if (!langs.value.includes(lang.value)) lang.value = def.value.language
  }
  if (next === 'yaml') source.value = asYaml()
  if (next === 'json') source.value = JSON.stringify(cleanDefinition(def.value), null, 2)
  parseError.value = null
  mode.value = next
}

// the scenario as a file: what «upload a file» takes back after it was edited by hand
function downloadFile() {
  downloadText(`${def.value.key || 'scenario'}.yaml`, asYaml(), 'text/yaml')
}

function parseSource(): any | null {
  try {
    const value = mode.value === 'yaml' ? yaml.load(source.value) : JSON.parse(source.value)
    if (!value || typeof value !== 'object') throw new Error(t('scenarios.not_an_object'))
    parseError.value = null
    return value
  } catch (e: any) {
    parseError.value = e?.message || String(e)
    return null
  }
}

// what goes to the server: YAML text as is (the server reports the line of an error)
function payload(): Record<string, any> | null {
  if (mode.value === 'yaml') return { yaml: source.value }
  if (mode.value === 'json') {
    const parsed = parseSource()
    return parsed ? { definition: parsed } : null
  }
  return { definition: cleanDefinition(def.value) }
}

async function check() {
  const body = payload()
  if (!body) return
  strict.value = true
  busy.value = true
  problem.value = null
  checkedOk.value = false
  try {
    const res = await api<any>(path('/scenarios/check'), { method: 'POST', body })
    warnings.value = res.warnings || []
    if (res.valid) checkedOk.value = true
    else problem.value = codeText(res.error)
  } catch (e) {
    problem.value = errorText(e)
  } finally {
    busy.value = false
  }
}

function askSave() {
  strict.value = true
  saveOpen.value = true
}

async function save() {
  const body = payload()
  if (!body) {
    saveOpen.value = false
    return
  }
  busy.value = true
  problem.value = null
  checkedOk.value = false
  try {
    const res = await api<any>(path('/scenarios'), {
      method: 'POST',
      body: { ...body, note: note.value || null, activate: activateOnSave.value, enable_in_channels: isNew.value ? enableIn.value : [] },
    })
    saveOpen.value = false
    note.value = ''
    warnings.value = res.warnings || []
    toast.ok(res.created ? t('scenarios.saved_version', { v: res.scenario.version }) : t('scenarios.unchanged'))
    left = true
    if (isNew.value) await navigateTo(`${base.value}/scenarios/${res.scenario.key}`, { replace: true })
    else {
      await load()
      await loadLists()
      left = false
    }
  } catch (e) {
    saveOpen.value = false
    problem.value = errorText(e)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  } finally {
    busy.value = false
  }
}

async function openVersion(version: number) {
  busy.value = true
  try {
    const res = await api<any>(path(`/scenarios/${props.scenarioKey}/versions/${version}`))
    def.value = toForm(res.scenario.definition)
    viewing.value = version
    mode.value = 'form'
    problem.value = null
    checkedOk.value = false
    saved.value = snapshot()
    versionsOpen.value = false
  } catch (e) {
    toast.error(errorText(e))
  } finally {
    busy.value = false
  }
}

async function activate(version: number) {
  if (!(await confirm.confirm({ message: t('scenarios.activate_confirm', { v: version }) }))) return
  busy.value = true
  try {
    await api(path(`/scenarios/${props.scenarioKey}/activate`), { method: 'POST', body: { version } })
    toast.ok(t('scenarios.activated', { v: version }))
    versionsOpen.value = false
    await load()
    await loadLists()
  } catch (e) {
    toast.error(errorText(e))
  } finally {
    busy.value = false
  }
}

async function disable() {
  if (!(await confirm.confirm({ message: t('scenarios.disable_confirm'), tone: 'danger', confirmLabel: t('scenarios.disable') }))) return
  busy.value = true
  try {
    await api(path(`/scenarios/${props.scenarioKey}/disable`), { method: 'POST' })
    toast.ok(t('scenarios.disabled'))
    versionsOpen.value = false
    await load()
  } catch (e) {
    toast.error(errorText(e))
  } finally {
    busy.value = false
  }
}

// what the selects of the steps offer; each list is optional for the editor to work
async function loadLists() {
  const quiet = async <T,>(job: () => Promise<T>, fallback: T): Promise<T> => {
    try {
      return await job()
    } catch {
      return fallback
    }
  }
  const list = await quiet(async () => await api<{ items: any[]; defaults?: { history_messages?: number } }>(path('/scenarios')), {
    items: [] as any[],
  })
  allScenarios.value = list.items
  defaults.value = list.defaults || {}
  if (!canEdit.value) return
  endpoints.value = await quiet(
    async () => (await api<{ items: any[] }>(path('/endpoints'))).items.filter((e) => e.is_active).map((e) => e.name),
    [],
  )
  notifiers.value = await quiet(
    async () => (await api<{ items: any[] }>(path('/notifiers'))).items.map((n) => ({ name: n.name, kind: n.kind })),
    [],
  )
  categories.value = await quiet(async () => {
    const res = await api<{ categories: { name: string | null }[] }>(path('/catalog'), { query: { limit: 1 } })
    return res.categories.map((c) => c.name).filter((c): c is string => !!c)
  }, [])
}

// --- leaving with changes -------------------------------------------------------------

// set while the editor itself navigates away after a save
let left = false
const dirty = computed(() => {
  if (!def.value || !canEdit.value) return false
  return mode.value === 'form' ? snapshot() !== saved.value : true
})
onBeforeRouteLeave(async () => {
  if (left || !dirty.value) return true
  return await confirm.confirm({ message: t('editor.unsaved'), confirmLabel: t('editor.unsaved_leave'), tone: 'danger' })
})
function beforeUnload(e: BeforeUnloadEvent) {
  if (!left && dirty.value) e.preventDefault()
}

onMounted(async () => {
  window.addEventListener('beforeunload', beforeUnload)
  await load()
  await loadLists()
  // a template brings its own key: never save over a scenario the tenant already has
  if (isNew.value && def.value?.key && allScenarios.value.some((x) => x.key === def.value.key)) {
    def.value.key = slugKey(def.value.key, allScenarios.value.map((x) => x.key), 'scenario')
  }
})
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))
</script>
