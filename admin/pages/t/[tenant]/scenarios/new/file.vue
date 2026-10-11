<template>
  <div class="max-w-[980px] mx-auto space-y-6">
    <!-- A scenario written as a file: chosen here, checked by the server at once, saved as
         the working version with one button. No editor in between. -->
    <header class="space-y-1.5">
      <NuxtLink :to="`${base}/scenarios`" class="link inline-flex items-center gap-1.5 text-sm">
        <UiIcon name="chevron-left" :size="16" /> {{ t('editor.all') }}
      </NuxtLink>
      <h1 class="text-[28px] leading-tight font-bold text-slate-900 dark:text-white">{{ t('editor.file.title') }}</h1>
      <p class="page-sub max-w-[62ch]">{{ t('editor.file.sub') }}</p>
    </header>

    <p v-if="loadError" class="alert-red" role="alert">{{ t('editor.file.load_error', { error: loadError }) }}</p>

    <label
      class="card card-body flex flex-col items-center gap-1.5 border-dashed text-center cursor-pointer transition-colors focus-within:ring-2 focus-within:ring-primary"
      :class="over ? '!border-primary-dark !bg-[#e8f2fc] dark:!bg-sky-500/10' : ''"
      @dragover.prevent="over = true"
      @dragleave.prevent="over = false"
      @drop.prevent="dropped"
    >
      <UiIcon name="upload" :size="28" class="muted" />
      <span class="text-[17px] font-semibold text-slate-900 dark:text-white">{{ t('editor.file.drop_title') }}</span>
      <span class="text-sm muted">{{ t('editor.file.drop_hint') }}</span>
      <span class="btn-secondary mt-2.5">{{ t('editor.file.choose') }}</span>
      <input type="file" class="sr-only" multiple :accept="SUFFIXES.join(',')" :disabled="busy" @change="chosen" />
    </label>

    <ul v-if="items.length" class="space-y-2.5">
      <li v-for="it in items" :key="it.id" class="card px-5 py-4 space-y-2.5">
        <div class="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <span class="flex-1 basis-[260px] min-w-0">
            <span class="block text-[17px] font-semibold text-slate-900 dark:text-white truncate">{{ it.title || it.name }}</span>
            <span v-if="it.key" class="block text-sm muted truncate">
              {{ it.name }} · <span class="code">{{ it.key }}</span> · {{ t('editor.file.questions', { n: it.questions }) }}
            </span>
          </span>
          <span :class="BADGES[it.state]" role="status">{{ stateText(it) }}</span>
          <NuxtLink v-if="isDone(it)" :to="`${base}/scenarios/${it.key}`" class="btn-secondary btn-sm">{{ t('scenarios.open') }}</NuxtLink>
          <button v-else type="button" class="btn-ghost btn-sm btn-icon" :aria-label="t('editor.file.remove')" :title="t('editor.file.remove')" :disabled="busy" @click="remove(it)">
            <UiIcon name="close" :size="16" />
          </button>
        </div>
        <p v-if="what(it)" class="text-sm text-slate-700 dark:text-slate-200">{{ what(it) }}</p>
        <p v-if="isDone(it) && !worksIn(it.key).length" class="alert-amber">{{ t('editor.file.what.nowhere') }}</p>
        <ul v-if="it.problems.length" class="alert-red space-y-1" role="alert">
          <li v-for="(p, i) in it.problems" :key="i" class="break-words">{{ p }}</li>
        </ul>
        <details v-if="it.warnings.length" class="alert-amber">
          <summary class="cursor-pointer font-medium">{{ t('editor.file.warnings', { n: remarks(it).length }) }}</summary>
          <ul class="mt-2 list-disc pl-5 space-y-0.5">
            <li v-for="(w, i) in remarks(it)" :key="i" class="break-words">{{ w }}</li>
          </ul>
        </details>
      </li>
    </ul>

    <section v-if="ready.length" class="card card-body space-y-4">
      <template v-if="needChannels">
        <div v-if="channels.length">
          <p class="label">{{ t('editor.file.channels') }}</p>
          <label v-for="c in channels" :key="c.id" class="flex items-center gap-2.5 min-h-[40px]">
            <input v-model="enableIn" type="checkbox" class="checkbox" :value="c.id" :disabled="busy" />
            {{ c.title }}
          </label>
          <p class="hint">{{ t('editor.file.channels_hint') }}</p>
        </div>
        <p v-else class="alert-amber">
          {{ t('editor.file.no_channels') }}
          <NuxtLink :to="`${base}/channels`" class="link">{{ t('nav.channels') }}</NuxtLink>
        </p>
      </template>
      <div class="flex flex-wrap items-center gap-x-4 gap-y-2">
        <button type="button" class="btn-primary !px-6" :disabled="busy || checking" @click="run">
          <UiIcon name="upload" :size="16" /> {{ t('editor.file.run') }}<template v-if="ready.length > 1"> ({{ ready.length }})</template>
        </button>
        <p v-if="bad.length" class="text-sm muted">{{ t('editor.file.skip_bad', { n: bad.length }) }}</p>
      </div>
    </section>

    <section v-else-if="uploaded.length && !checking" class="alert-green space-y-2.5" role="status">
      <p class="font-semibold">{{ t('editor.file.done') }}</p>
      <p v-if="replaced">{{ t('editor.file.done_note') }}</p>
      <div class="flex flex-wrap gap-2.5">
        <NuxtLink :to="`${base}/sandbox`" class="btn-secondary btn-sm">{{ t('editor.file.try') }}</NuxtLink>
        <NuxtLink :to="`${base}/scenarios`" class="btn-secondary btn-sm">{{ t('editor.all') }}</NuxtLink>
      </div>
    </section>

    <section class="card card-body space-y-3">
      <h2 class="card-title">{{ t('editor.file.how_title') }}</h2>
      <p class="text-sm text-slate-600 dark:text-slate-300 max-w-[72ch]">{{ t('editor.file.how_sub') }}</p>
      <p class="text-sm text-slate-600 dark:text-slate-300 max-w-[72ch]">{{ t('editor.file.how_existing') }}</p>
      <div class="flex flex-wrap gap-2.5">
        <NuxtLink :to="`${base}/scenarios/new/format`" class="btn-secondary">{{ t('editor.file.format') }}</NuxtLink>
        <button type="button" class="btn-secondary" :disabled="fetching" @click="example">{{ t('editor.file.example') }}</button>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { downloadText } from '~/utils/download'
import { pickText } from '~/utils/texts'

const SUFFIXES = ['.yaml', '.yml', '.json']
// as much as the server takes as one scenario
const MAX_CHARS = 500_000

type State = 'checking' | 'ok' | 'bad' | 'saving' | 'saved' | 'same' | 'failed'
type Item = {
  id: number
  // the file's name
  name: string
  state: State
  // what goes to the server: the YAML text as it is, or the object of a JSON file
  body: Record<string, any> | null
  key: string | null
  title: string
  questions: number
  problems: string[]
  warnings: any[]
  // the version the upload made (or the working one, when the file changed nothing)
  version: number | null
  // the file went to the server (a file equal to the working version has nothing to send)
  sent: boolean
  // the scenario was working before this upload, so dialogues may be going by it
  replacedLive: boolean
}

const BADGES: Record<State, string> = {
  checking: 'badge-gray',
  ok: 'badge-blue',
  bad: 'badge-red',
  saving: 'badge-gray',
  saved: 'badge-green',
  same: 'badge-gray',
  failed: 'badge-red',
}

const { t, locale } = useI18n()
const api = useApi()
const toast = useToast()
const { errorText, codeText, problemLines } = useErrorText()
const { base, path } = useTenant()
const { channels, load: loadInfo } = useTenantInfo()
usePage(() => [{ label: t('nav.scenarios'), to: `${base.value}/scenarios` }, { label: t('editor.file.title') }])

const items = ref<Item[]>([])
// the tenant's scenarios by key: [{key, title, active_version, latest_version, channels}]
const existing = ref<Record<string, any>>({})
const enableIn = ref<string[]>([])
const loadError = ref('')
const busy = ref(false)
const over = ref(false)
const fetching = ref(false)
let nextId = 0

const ready = computed(() => items.value.filter((it) => it.state === 'ok'))
const bad = computed(() => items.value.filter((it) => it.state === 'bad'))
const uploaded = computed(() => items.value.filter((it) => it.sent && isDone(it)))
const checking = computed(() => items.value.some((it) => it.state === 'checking'))
const replaced = computed(() => uploaded.value.some((it) => it.replacedLive))
const needChannels = computed(() => ready.value.some((it) => !worksIn(it.key).length))

function isDone(it: Item): boolean {
  return it.state === 'saved' || it.state === 'same'
}
// the channels a scenario is switched on in
function worksIn(key: string | null): any[] {
  return ((key && existing.value[key]?.channels) || []).filter((c: any) => c.is_enabled)
}

async function loadExisting() {
  const res = await api<{ items: any[] }>(path('/scenarios'))
  existing.value = Object.fromEntries(res.items.map((s) => [s.key, s]))
}

onMounted(async () => {
  try {
    await Promise.all([loadInfo(), loadExisting()])
  } catch (e) {
    loadError.value = errorText(e)
  }
  // an uploaded scenario works where the bot works, unless the person unticks a channel
  enableIn.value = channels.value.map((c: any) => c.id)
})

// --- choosing and checking -------------------------------------------------------------

function chosen(e: Event) {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files || [])
  // the same file can be chosen again after it was corrected
  input.value = ''
  return add(files)
}
function dropped(e: DragEvent) {
  over.value = false
  if (busy.value) return
  return add(Array.from(e.dataTransfer?.files || []))
}

async function add(files: File[]) {
  for (const file of files) {
    items.value.push({
      id: nextId++,
      name: file.name,
      state: 'checking',
      body: null,
      key: null,
      title: '',
      questions: 0,
      problems: [],
      warnings: [],
      version: null,
      sent: false,
      replacedLive: false,
    })
    await check(items.value[items.value.length - 1], file)
  }
}

async function check(it: Item, file: File) {
  const fail = (...problems: string[]) => {
    it.state = 'bad'
    it.problems = problems
  }
  const suffix = file.name.slice(file.name.lastIndexOf('.')).toLowerCase()
  if (!SUFFIXES.includes(suffix)) return fail(t('editor.file.bad_type'))
  // a letter takes up to four bytes: a file bigger than this is over the limit for sure
  if (file.size > MAX_CHARS * 4) return fail(t('editor.file.too_big'))
  let text: string
  try {
    // an editor on Windows starts a file with an invisible mark of its encoding
    text = (await file.text()).replace(/^﻿/, '')
  } catch {
    return fail(t('editor.file.unreadable'))
  }
  if (text.length > MAX_CHARS) return fail(t('editor.file.too_big'))
  if (suffix === '.json') {
    try {
      it.body = { definition: JSON.parse(text) }
    } catch (e: any) {
      return fail(t('editor.file.bad_json', { error: e?.message || String(e) }))
    }
  } else {
    // the text goes as it is: the server names the line of a mistake
    it.body = { yaml: text }
  }
  try {
    const res = await api<any>(path('/scenarios/check'), { method: 'POST', body: it.body })
    if (!res.valid) return fail(...problemLines(res.error))
    const others = items.value.filter((other) => other !== it && other.key === res.key)
    if (others.some((other) => other.state === 'ok' || other.state === 'saving')) {
      return fail(t('editor.file.same_key', { key: res.key }))
    }
    // a corrected file chosen again takes the place of its earlier upload
    items.value = items.value.filter((other) => !others.includes(other))
    it.key = res.key
    it.title = pickText(res.definition.title, locale.value, res.definition.language)
    it.questions = res.definition.fields.length
    it.warnings = res.warnings || []
    // the same as the working version: nothing to upload
    it.version = res.unchanged ? existing.value[res.key]?.active_version ?? null : null
    it.state = res.unchanged ? 'same' : 'ok'
  } catch (e) {
    fail(errorText(e))
  }
}

function remove(it: Item) {
  items.value = items.value.filter((other) => other !== it)
}

// --- what a file is and what the upload does -------------------------------------------

function stateText(it: Item): string {
  if (it.state === 'ok') return t(it.key && existing.value[it.key] ? 'editor.file.state.version' : 'editor.file.state.new')
  return t(`editor.file.state.${it.state}`, { v: it.version })
}

function what(it: Item): string {
  const was = it.key ? existing.value[it.key] : null
  if (it.state === 'ok') {
    if (!was) return t('editor.file.what.new')
    const params = { next: was.latest_version + 1, title: was.title || was.key, v: was.active_version }
    return t(was.active_version ? 'editor.file.what.version' : 'editor.file.what.version_off', params)
  }
  if (it.state === 'same') return t('editor.file.what.same', { v: it.version })
  return ''
}

// The remarks of a file. «No text in a language» comes once per text — said once per
// language here, with the places in a row.
function remarks(it: Item): string[] {
  const lines: string[] = []
  const places: Record<string, string[]> = {}
  for (const w of it.warnings) {
    if (w.code === 'scenario.text_missing_language') (places[w.params.language] ||= []).push(w.params.path)
    else lines.push(codeText(w))
  }
  for (const [language, paths] of Object.entries(places)) {
    lines.push(t('editor.file.no_language', { language, n: paths.length, paths: paths.join(', ') }))
  }
  return lines
}

// --- uploading --------------------------------------------------------------------------

async function run() {
  if (busy.value) return
  busy.value = true
  try {
    for (const it of ready.value) {
      it.state = 'saving'
      it.sent = true
      it.replacedLive = !!existing.value[it.key!]?.active_version
      try {
        const res = await api<any>(path('/scenarios'), {
          method: 'POST',
          body: {
            ...it.body,
            note: t('editor.file.note', { name: it.name }),
            activate: true,
            // a scenario that works somewhere keeps its channels as they are
            enable_in_channels: worksIn(it.key).length ? [] : enableIn.value,
          },
        })
        // where the scenario works now — before the card says «works»
        await loadExisting().catch(() => {})
        it.version = res.scenario.version
        it.warnings = res.warnings || []
        it.state = res.created ? 'saved' : 'same'
        if (!res.created) it.replacedLive = false
      } catch (e) {
        it.state = 'failed'
        it.problems = [errorText(e)]
      }
    }
  } finally {
    busy.value = false
  }
}

// --- the example file --------------------------------------------------------------------

async function example() {
  fetching.value = true
  try {
    const res = await api<{ example: string }>(path('/scenario-format'))
    downloadText('scenario-example.yaml', res.example, 'text/yaml')
  } catch (e) {
    toast.error(errorText(e))
  } finally {
    fetching.value = false
  }
}
</script>
