<template>
  <div class="space-y-5">
    <UiState :loading="loading" :error="error" :has-data="ready" :retry="load">
      <template v-if="ready">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <h1 class="page-title truncate">{{ isNew ? t('scenarios.new') : titleText }}</h1>
            <p class="page-sub">
              <span class="code">{{ def.key || '—' }}</span>
              <template v-if="!isNew">
                ·
                <span v-if="activeVersion" class="badge-green">{{ t('scenarios.active_v', { v: activeVersion }) }}</span>
                <span v-else class="badge-gray">{{ t('scenarios.off') }}</span>
                <span v-if="viewing && viewing !== activeVersion" class="badge-amber ml-1">{{ t('scenarios.viewing_v', { v: viewing }) }}</span>
              </template>
            </p>
          </div>
          <div class="flex flex-wrap gap-2">
            <button type="button" class="btn-secondary" :disabled="busy" @click="check">
              <UiIcon name="check" :size="16" /> {{ t('scenarios.check') }}
            </button>
            <button v-if="canEdit" type="button" class="btn-primary" :disabled="busy" @click="saveOpen = true">
              {{ t('common.save') }}
            </button>
          </div>
        </div>

        <!-- check result: an error (stops saving) and warnings (only shown) -->
        <div v-if="problem" class="alert-red">{{ problem }}</div>
        <div v-if="warnings.length" class="alert-amber">
          <p class="font-medium">{{ t('scenarios.warnings') }}</p>
          <ul class="mt-1 list-disc pl-5 space-y-0.5 text-sm">
            <li v-for="(w, i) in warnings" :key="i">{{ codeText(w) }}</li>
          </ul>
        </div>
        <div v-if="checkedOk && !problem" class="alert-green">{{ t('scenarios.check_ok') }}</div>

        <div class="grid gap-5 xl:grid-cols-4">
          <div class="xl:col-span-3 space-y-4">
            <div class="tabs">
              <button v-for="m in MODES" :key="m" type="button" class="tab" :class="mode === m ? 'tab-active' : ''" @click="switchMode(m)">
                {{ t(`scenarios.mode.${m}`) }}
              </button>
            </div>
            <ScenarioForm
              v-if="mode === 'form'"
              :def="def"
              :languages="languages"
              :endpoints="endpoints"
              :notifiers="notifiers"
              :categories="categories"
              :scenario-keys="scenarioKeys"
              :is-new="isNew"
              :readonly="!canEdit"
            />
            <div v-else class="card card-body">
              <p class="hint mb-2">{{ mode === 'yaml' ? t('scenarios.yaml_hint') : t('scenarios.json_hint') }}</p>
              <textarea
                v-model="source"
                class="textarea code text-xs leading-5 min-h-[32rem]"
                spellcheck="false"
                :readonly="!canEdit"
              />
              <p v-if="parseError" class="alert-red mt-2">{{ parseError }}</p>
            </div>
          </div>

          <!-- versions -->
          <aside v-if="!isNew" class="space-y-4">
            <div class="card card-body">
              <h2 class="card-title mb-3">{{ t('scenarios.versions') }}</h2>
              <ul class="space-y-2">
                <li v-for="v in versions" :key="v.version" class="rounded-button border border-slate-200 dark:border-slate-700 p-2.5 text-sm">
                  <div class="flex items-center gap-2">
                    <span class="font-semibold">v{{ v.version }}</span>
                    <span v-if="v.is_active" class="badge-green">{{ t('scenarios.active') }}</span>
                    <span class="ml-auto text-xs muted">{{ f.date(v.created_at) }}</span>
                  </div>
                  <p class="text-xs muted mt-0.5 truncate">{{ v.author || '—' }}</p>
                  <p v-if="v.note" class="text-xs mt-1 whitespace-pre-line">{{ v.note }}</p>
                  <div class="mt-2 flex flex-wrap gap-1">
                    <button type="button" class="btn-ghost btn-sm" @click="openVersion(v.version)">{{ t('scenarios.open') }}</button>
                    <button v-if="canEdit && !v.is_active" type="button" class="btn-ghost btn-sm" :disabled="busy" @click="activate(v.version)">
                      <UiIcon name="rollback" :size="14" /> {{ t('scenarios.activate') }}
                    </button>
                  </div>
                </li>
              </ul>
              <button v-if="canEdit && activeVersion" type="button" class="btn-danger btn-sm mt-4 w-full" :disabled="busy" @click="disable">
                {{ t('scenarios.disable') }}
              </button>
            </div>
          </aside>
        </div>
      </template>
    </UiState>

    <!-- save -->
    <UiModal :open="saveOpen" :title="t('scenarios.save_title')" @close="saveOpen = false">
      <div class="space-y-4">
        <div>
          <label class="label">{{ t('scenarios.note') }}</label>
          <textarea v-model="note" rows="2" class="textarea" :placeholder="t('scenarios.note_placeholder')" />
        </div>
        <label class="flex items-center gap-2 text-sm">
          <input v-model="activateOnSave" type="checkbox" class="checkbox" />
          {{ t('scenarios.activate_on_save') }}
        </label>
        <div v-if="isNew && channels.length">
          <p class="label">{{ t('scenarios.enable_in') }}</p>
          <label v-for="c in channels" :key="c.id" class="flex items-center gap-2 text-sm py-0.5">
            <input v-model="enableIn" type="checkbox" class="checkbox" :value="c.id" />
            {{ c.title }}
          </label>
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
import { cleanDefinition, newDefinition, toForm } from '~/utils/scenario'

const MODES = ['form', 'yaml', 'json'] as const
type Mode = (typeof MODES)[number]

const { t } = useI18n()
const api = useApi()
const f = useFormat()
const route = useRoute()
const toast = useToast()
const confirm = useConfirm()
const { errorText, codeText } = useErrorText()
const { base, path, can } = useTenant()
const { channels, load: loadInfo } = useTenantInfo()

const routeKey = computed(() => String(route.params.key))
const isNew = computed(() => routeKey.value === 'new')
const canEdit = computed(() => can('admin'))

const def = ref<any>(null)
const languages = ref<string[]>([])
const versions = ref<any[]>([])
const endpoints = ref<string[]>([])
const notifiers = ref<{ name: string; kind: string }[]>([])
const categories = ref<string[]>([])
const scenarioKeys = ref<string[]>([])
const viewing = ref<number | null>(null)
const mode = ref<Mode>('form')
const source = ref('')
const parseError = ref<string | null>(null)
const problem = ref<string | null>(null)
const warnings = ref<any[]>([])
const checkedOk = ref(false)
const busy = ref(false)
const saveOpen = ref(false)
const note = ref('')
const activateOnSave = ref(true)
const enableIn = ref<string[]>([])
const ready = computed(() => !!def.value)
const activeVersion = computed(() => versions.value.find((v) => v.is_active)?.version || null)
const titleText = computed(() => {
  const v = def.value?.title
  if (typeof v === 'string') return v
  return (v && (v[def.value.language] || Object.values(v)[0])) || def.value?.key || ''
})

usePage(() => [
  { label: t('nav.scenarios'), to: `${base.value}/scenarios` },
  { label: isNew.value ? t('scenarios.new') : routeKey.value },
])

const { loading, error, load } = useLoader(async () => {
  const info = await loadInfo()
  languages.value = info?.effective_settings?.languages || []
  if (isNew.value) {
    def.value = toForm(newDefinition(languages.value[0] || 'en'))
    versions.value = []
  } else {
    const res = await api<any>(path(`/scenarios/${routeKey.value}`))
    languages.value = res.languages
    versions.value = res.versions
    def.value = toForm(res.current.definition)
    viewing.value = res.current.version
    warnings.value = res.warnings
  }
  return def.value
})

// form ⇄ text: the text is parsed only when leaving its tab
function switchMode(next: Mode) {
  if (next === mode.value) return
  if (mode.value !== 'form') {
    const parsed = parseSource()
    if (!parsed) return
    def.value = toForm(parsed)
  }
  if (next === 'yaml') source.value = yaml.dump(cleanDefinition(def.value), { lineWidth: 100, noRefs: true })
  if (next === 'json') source.value = JSON.stringify(cleanDefinition(def.value), null, 2)
  parseError.value = null
  mode.value = next
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

async function save() {
  const body = payload()
  if (!body) return
  busy.value = true
  problem.value = null
  checkedOk.value = false
  try {
    const res = await api<any>(path('/scenarios'), {
      method: 'POST',
      body: { ...body, note: note.value || null, activate: activateOnSave.value, enable_in_channels: enableIn.value },
    })
    saveOpen.value = false
    note.value = ''
    warnings.value = res.warnings || []
    toast.ok(res.created ? t('scenarios.saved_version', { v: res.scenario.version }) : t('scenarios.unchanged'))
    if (isNew.value) await navigateTo(`${base.value}/scenarios/${res.scenario.key}`, { replace: true })
    else await load()
  } catch (e) {
    saveOpen.value = false
    problem.value = errorText(e)
  } finally {
    busy.value = false
  }
}

async function openVersion(version: number) {
  busy.value = true
  try {
    const res = await api<any>(path(`/scenarios/${routeKey.value}/versions/${version}`))
    def.value = toForm(res.scenario.definition)
    viewing.value = version
    mode.value = 'form'
    problem.value = null
    checkedOk.value = false
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
    await api(path(`/scenarios/${routeKey.value}/activate`), { method: 'POST', body: { version } })
    toast.ok(t('scenarios.activated', { v: version }))
    await load()
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
    await api(path(`/scenarios/${routeKey.value}/disable`), { method: 'POST' })
    toast.ok(t('scenarios.disabled'))
    await load()
  } catch (e) {
    toast.error(errorText(e))
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  await load()
  if (canEdit.value) {
    try {
      endpoints.value = (await api<{ items: any[] }>(path('/endpoints'))).items.filter((e) => e.is_active).map((e) => e.name)
    } catch {
      endpoints.value = []
    }
    try {
      notifiers.value = (await api<{ items: any[] }>(path('/notifiers'))).items.map((n) => ({ name: n.name, kind: n.kind }))
    } catch {
      notifiers.value = []
    }
    try {
      scenarioKeys.value = (await api<{ items: { key: string }[] }>(path('/scenarios'))).items.map((x) => x.key)
    } catch {
      scenarioKeys.value = []
    }
    try {
      const res = await api<{ categories: { name: string | null }[] }>(path('/catalog'), { query: { limit: 1 } })
      categories.value = res.categories.map((c) => c.name).filter((c): c is string => !!c)
    } catch {
      categories.value = []
    }
  }
})
</script>
