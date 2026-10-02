<template>
  <UiState :loading="loading" :error="error" :has-data="loaded" :retry="load">
    <div class="space-y-5">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 class="page-title code">{{ promptKey }}</h1>
          <p class="page-sub">
            <template v-if="tenantId">
              <span v-if="ownActive" class="badge-violet">{{ t('prompts.uses_own', { v: ownActive.version }) }}</span>
              <span v-else class="badge-gray">{{ t('prompts.uses_shared', { v: sharedActive?.version ?? '—' }) }}</span>
            </template>
            <span v-else-if="ownActive" class="badge-green">{{ t('prompts.active_v', { v: ownActive.version }) }}</span>
          </p>
        </div>
        <div class="flex flex-wrap gap-2">
          <button v-if="tenantId && ownActive && canWrite" type="button" class="btn-secondary" :disabled="busy" @click="resetOverride">
            <UiIcon name="rollback" :size="16" /> {{ t('prompts.reset_to_shared') }}
          </button>
          <button type="button" class="btn-secondary" @click="showDiff = !showDiff">
            {{ showDiff ? t('prompts.hide_diff') : t('prompts.show_diff') }}
          </button>
          <button v-if="canWrite" type="button" class="btn-primary" :disabled="busy || !changed" @click="saveOpen = true">
            {{ t('prompts.save_version') }}
          </button>
        </div>
      </div>

      <p v-if="tenantId && !ownActive" class="alert-blue">{{ t('prompts.override_hint') }}</p>
      <p v-if="!canWrite" class="alert-amber">{{ t('prompts.read_only') }}</p>

      <div class="grid gap-5 xl:grid-cols-4">
        <div class="xl:col-span-3 space-y-4">
          <div v-if="showDiff" class="card card-body">
            <div class="flex flex-wrap items-center gap-2 mb-2 text-sm">
              <span class="muted">{{ t('prompts.compare_with') }}</span>
              <select v-model="compareId" class="select h-8 w-auto">
                <option v-for="o in compareOptions" :key="o.id" :value="o.id">{{ o.label }}</option>
              </select>
              <span class="muted">→ {{ t('prompts.editor') }}</span>
            </div>
            <DiffView :before="compareBody" :after="body" />
          </div>
          <div class="card card-body">
            <textarea
              v-model="body"
              class="textarea code text-xs leading-5 min-h-[36rem]"
              spellcheck="false"
              :readonly="!canWrite"
            />
            <p class="hint mt-2">{{ t('prompts.placeholders_hint') }}</p>
          </div>
        </div>

        <aside class="card card-body h-fit">
          <h2 class="card-title mb-3">{{ t('prompts.versions') }}</h2>
          <ul class="space-y-2">
            <li v-for="v in versions" :key="v.id" class="rounded-button border border-slate-200 dark:border-slate-700 p-2.5 text-sm">
              <div class="flex items-center gap-2">
                <span class="font-semibold">v{{ v.version }}</span>
                <span v-if="v.is_active" class="badge-green">{{ t('prompts.active') }}</span>
                <span class="ml-auto text-xs muted">{{ f.date(v.created_at) }}</span>
              </div>
              <p class="text-xs muted truncate">{{ v.author || '—' }}</p>
              <p v-if="v.note" class="text-xs mt-1 whitespace-pre-line">{{ v.note }}</p>
              <div class="mt-2 flex flex-wrap gap-1">
                <button type="button" class="btn-ghost btn-sm" @click="body = v.body">{{ t('prompts.load') }}</button>
                <button type="button" class="btn-ghost btn-sm" @click="compare(v.id)">{{ t('prompts.diff') }}</button>
                <button v-if="canWrite && !v.is_active" type="button" class="btn-ghost btn-sm" :disabled="busy" @click="activate(v.version)">
                  {{ t('prompts.activate') }}
                </button>
              </div>
            </li>
          </ul>
          <p v-if="!versions.length" class="text-sm muted">{{ t('prompts.no_own_versions') }}</p>
          <div v-if="sharedActive && tenantId" class="mt-4 pt-4 border-t border-slate-200 dark:border-slate-700 text-sm">
            <p class="font-medium">{{ t('prompts.shared_active', { v: sharedActive.version }) }}</p>
            <div class="mt-2 flex flex-wrap gap-1">
              <button type="button" class="btn-ghost btn-sm" @click="body = sharedActive.body">{{ t('prompts.load') }}</button>
              <button type="button" class="btn-ghost btn-sm" @click="compare(sharedActive.id)">{{ t('prompts.diff') }}</button>
            </div>
          </div>
        </aside>
      </div>

      <UiModal :open="saveOpen" :title="t('prompts.save_version')" @close="saveOpen = false">
        <div class="space-y-4">
          <div>
            <label class="label">{{ t('scenarios.note') }}</label>
            <textarea v-model="note" rows="2" class="textarea" :placeholder="t('scenarios.note_placeholder')" />
          </div>
          <label class="flex items-center gap-2 text-sm">
            <input v-model="activateOnSave" type="checkbox" class="checkbox" />
            {{ t('prompts.activate_on_save') }}
          </label>
          <p v-if="saveError" class="alert-red">{{ saveError }}</p>
        </div>
        <template #footer>
          <button type="button" class="btn-secondary" @click="saveOpen = false">{{ t('common.cancel') }}</button>
          <button type="button" class="btn-primary" :disabled="busy" @click="save">{{ t('common.save') }}</button>
        </template>
      </UiModal>
    </div>
  </UiState>
</template>

<script setup lang="ts">
const props = defineProps<{ promptKey: string; tenantId: string | null; canWrite: boolean }>()
const { t } = useI18n()
const api = useApi()
const f = useFormat()
const toast = useToast()
const confirm = useConfirm()
const { errorText } = useErrorText()

const versions = ref<any[]>([])
const sharedActive = ref<any>(null)
const body = ref('')
const loaded = ref(false)
const showDiff = ref(false)
const compareId = ref('')
const busy = ref(false)
const saveOpen = ref(false)
const note = ref('')
const activateOnSave = ref(true)
const saveError = ref<string | null>(null)

const ownActive = computed(() => versions.value.find((v) => v.is_active) || null)
// what the bot uses now: the editor starts from it, "changed" compares with it
const baseline = computed(() => ownActive.value || sharedActive.value || versions.value[0] || null)
const changed = computed(() => body.value !== (baseline.value?.body ?? ''))
const compareOptions = computed(() => {
  const out = versions.value.map((v) => ({ id: v.id, label: `v${v.version}${v.is_active ? ' ✓' : ''}`, body: v.body }))
  if (sharedActive.value && props.tenantId) {
    out.push({ id: sharedActive.value.id, label: t('prompts.shared_active', { v: sharedActive.value.version }), body: sharedActive.value.body })
  }
  return out
})
const compareBody = computed(() => compareOptions.value.find((o) => o.id === compareId.value)?.body ?? '')
const scope = computed(() => (props.tenantId ? { tenant_id: props.tenantId } : {}))

const { loading, error, load } = useLoader(async () => {
  const res = await api<any>(`/prompts/${props.promptKey}/versions`, { query: scope.value })
  versions.value = res.items
  sharedActive.value = res.shared_active || null
  body.value = baseline.value?.body ?? ''
  compareId.value = baseline.value?.id || ''
  loaded.value = true
  return res
})

function compare(id: string) {
  compareId.value = id
  showDiff.value = true
}

async function save() {
  busy.value = true
  saveError.value = null
  try {
    await api(`/prompts/${props.promptKey}/versions`, {
      method: 'POST',
      body: { ...scope.value, body: body.value, note: note.value || null, activate: activateOnSave.value },
    })
    saveOpen.value = false
    note.value = ''
    toast.ok(t('common.saved'))
    await load()
  } catch (e) {
    saveError.value = errorText(e)
  } finally {
    busy.value = false
  }
}

async function activate(version: number) {
  if (!(await confirm.confirm({ message: t('prompts.activate_confirm', { v: version }) }))) return
  busy.value = true
  try {
    await api(`/prompts/${props.promptKey}/activate`, { method: 'POST', body: { ...scope.value, version } })
    toast.ok(t('prompts.activated', { v: version }))
    await load()
  } catch (e) {
    toast.error(errorText(e))
  } finally {
    busy.value = false
  }
}

async function resetOverride() {
  if (!(await confirm.confirm({ message: t('prompts.reset_confirm'), tone: 'danger' }))) return
  busy.value = true
  try {
    await api(`/prompts/${props.promptKey}/override`, { method: 'DELETE', query: scope.value })
    toast.ok(t('prompts.reset_done'))
    await load()
  } catch (e) {
    toast.error(errorText(e))
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>
