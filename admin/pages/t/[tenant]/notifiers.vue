<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="page-title">{{ t('nav.notifiers') }}</h1>
        <p class="page-sub">{{ t('notifiers.sub') }}</p>
      </div>
      <button type="button" class="btn-primary" @click="openForm(null)">
        <UiIcon name="plus" :size="16" /> {{ t('notifiers.add') }}
      </button>
    </div>

    <div class="card overflow-hidden">
      <UiState :loading="loading" :error="error" :empty="!loading && !items.length ? t('notifiers.empty') : false" :has-data="items.length > 0" :retry="load">
        <div class="overflow-x-auto">
          <table class="table">
            <thead>
              <tr>
                <th>{{ t('notifiers.name') }}</th>
                <th>{{ t('notifiers.kind_label') }}</th>
                <th>{{ t('notifiers.targets') }}</th>
                <th>{{ t('notifiers.events') }}</th>
                <th>{{ t('endpoints.status') }}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              <tr v-for="n in items" :key="n.id">
                <td class="code font-medium">{{ n.name }}</td>
                <td><span class="badge-gray">{{ t(`notifiers.kind.${n.kind}`) }}</span> <span class="text-xs muted uppercase">{{ n.language }}</span></td>
                <td class="text-sm break-all">{{ n.targets.join(', ') }}</td>
                <td>
                  <span v-for="ev in n.events" :key="ev" class="badge-blue mr-1">{{ t(`notifiers.event.${ev}`) }}</span>
                  <span v-if="!n.events.length" class="text-xs muted">{{ t('notifiers.only_actions') }}</span>
                </td>
                <td>
                  <span :class="n.is_active ? 'badge-green' : 'badge-gray'">{{ n.is_active ? t('endpoints.active') : t('endpoints.off') }}</span>
                </td>
                <td class="text-right whitespace-nowrap">
                  <button type="button" class="btn-ghost btn-sm" :disabled="testing === n.id" @click="test(n)">
                    <UiIcon name="send" :size="14" /> {{ t('notifiers.test') }}
                  </button>
                  <button type="button" class="btn-icon btn-ghost btn-sm" :aria-label="t('common.edit')" @click="openForm(n)">
                    <UiIcon name="edit" :size="16" />
                  </button>
                  <button type="button" class="btn-icon btn-ghost btn-sm text-rose-600" :aria-label="t('common.delete')" @click="remove(n)">
                    <UiIcon name="trash" :size="16" />
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </UiState>
    </div>

    <div class="card card-body text-sm space-y-2">
      <h2 class="card-title">{{ t('notifiers.how_title') }}</h2>
      <p class="muted whitespace-pre-line">{{ t('notifiers.how_text') }}</p>
      <pre class="pre">- on: completed
  do: notify_telegram
  params:
    notifier: team</pre>
    </div>

    <UiModal :open="formOpen" :title="editing ? t('notifiers.edit', { name: editing.name }) : t('notifiers.add')" size="lg" @close="formOpen = false">
      <form id="notifier-form" class="space-y-4" @submit.prevent="submit">
        <div class="grid gap-4 md:grid-cols-3">
          <div>
            <label class="label">{{ t('notifiers.name') }}</label>
            <input v-model.trim="form.name" class="input code" required pattern="[a-z0-9_][a-z0-9_\-]{0,99}" :readonly="!!editing" />
            <p class="hint">{{ t('notifiers.name_hint') }}</p>
          </div>
          <div>
            <label class="label">{{ t('notifiers.kind_label') }}</label>
            <select v-model="form.kind" class="select" :disabled="!!editing">
              <option value="telegram">{{ t('notifiers.kind.telegram') }}</option>
              <option value="email">{{ t('notifiers.kind.email') }}</option>
            </select>
          </div>
          <div>
            <label class="label">{{ t('notifiers.language') }}</label>
            <select v-model="form.language" class="select">
              <option v-for="l in LANGS" :key="l" :value="l">{{ l }}</option>
            </select>
          </div>
        </div>

        <div v-if="form.kind === 'telegram'">
          <label class="label">{{ t('notifiers.bot_token') }}</label>
          <input
            v-model.trim="form.secret"
            type="password"
            autocomplete="new-password"
            class="input"
            :required="!editing"
            minlength="20"
            :placeholder="editing ? t('endpoints.secret_keep') : '123456:ABC…'"
          />
          <p class="hint">{{ t('notifiers.bot_token_hint') }}</p>
        </div>

        <div>
          <label class="label">{{ form.kind === 'telegram' ? t('notifiers.chat_ids') : t('notifiers.emails') }}</label>
          <textarea v-model="form.targets" rows="3" class="textarea code" required />
          <p class="hint">{{ form.kind === 'telegram' ? t('notifiers.chat_ids_hint') : t('notifiers.emails_hint') }}</p>
        </div>

        <div>
          <label class="label">{{ t('notifiers.events') }}</label>
          <div class="flex flex-wrap gap-4">
            <label v-for="ev in events" :key="ev" class="flex items-center gap-2 text-sm">
              <input v-model="form.events" type="checkbox" class="checkbox" :value="ev" />
              {{ t(`notifiers.event.${ev}`) }}
            </label>
          </div>
          <p class="hint">{{ t('notifiers.events_hint') }}</p>
        </div>

        <label class="flex items-center gap-2 text-sm">
          <input v-model="form.is_active" type="checkbox" class="checkbox" />
          {{ t('endpoints.active') }}
        </label>
        <p v-if="formError" class="alert-red">{{ formError }}</p>
      </form>
      <template #footer>
        <button type="button" class="btn-secondary" @click="formOpen = false">{{ t('common.cancel') }}</button>
        <button type="submit" form="notifier-form" class="btn-primary" :disabled="busy">{{ t('common.save') }}</button>
      </template>
    </UiModal>

    <UiModal :open="!!testResults" :title="t('notifiers.test_title')" @close="testResults = null">
      <ul class="space-y-2 text-sm">
        <li v-for="r in testResults || []" :key="r.target" class="flex items-start gap-2">
          <span :class="r.ok ? 'badge-green' : 'badge-red'">{{ r.ok ? t('notifiers.test_ok') : t('notifiers.test_failed') }}</span>
          <span class="code break-all">{{ r.target }}</span>
          <span v-if="r.error" class="text-rose-600 break-words">{{ r.error }}</span>
        </li>
      </ul>
    </UiModal>
  </div>
</template>

<script setup lang="ts">
const { t } = useI18n()
const api = useApi()
const toast = useToast()
const confirm = useConfirm()
const { errorText } = useErrorText()
const { path } = useTenant()
const { run } = useAction()
usePage(() => [{ label: t('nav.notifiers') }])

const LANGS = ['en', 'uk', 'ru']
const items = ref<any[]>([])
const events = ref<string[]>([])
const formOpen = ref(false)
const editing = ref<any>(null)
const busy = ref(false)
const formError = ref<string | null>(null)
const testing = ref<string | null>(null)
const testResults = ref<any[] | null>(null)
const form = reactive({
  name: '',
  kind: 'telegram' as 'telegram' | 'email',
  language: 'en',
  secret: '',
  targets: '',
  events: [] as string[],
  is_active: true,
})

const { loading, error, load } = useLoader(async () => {
  const res = await api<{ items: any[]; events: string[] }>(path('/notifiers'))
  items.value = res.items
  events.value = res.events
  return res
})

function openForm(n: any | null) {
  editing.value = n
  Object.assign(form, {
    name: n?.name || '',
    kind: n?.kind || 'telegram',
    language: n?.language || 'en',
    secret: '',
    targets: (n?.targets || []).join('\n'),
    events: n ? [...n.events] : ['handover', 'choose_reply', 'channel_error'],
    is_active: n ? n.is_active : true,
  })
  formError.value = null
  formOpen.value = true
}

function targetList() {
  return form.targets.split(/[\n,;]+/).map((x) => x.trim()).filter(Boolean)
}

async function submit() {
  busy.value = true
  formError.value = null
  try {
    if (editing.value) {
      const body: Record<string, any> = {
        targets: targetList(),
        events: form.events,
        language: form.language,
        is_active: form.is_active,
      }
      if (form.secret) body.secret = form.secret
      await api(path(`/notifiers/${editing.value.id}`), { method: 'PATCH', body })
    } else {
      await api(path('/notifiers'), {
        method: 'POST',
        body: {
          name: form.name,
          kind: form.kind,
          targets: targetList(),
          secret: form.kind === 'telegram' ? form.secret : null,
          events: form.events,
          language: form.language,
          is_active: form.is_active,
        },
      })
    }
    formOpen.value = false
    toast.ok(t('common.saved'))
    load()
  } catch (e) {
    formError.value = errorText(e)
  } finally {
    busy.value = false
  }
}

async function test(n: any) {
  testing.value = n.id
  const res = await run(() => api<{ results: any[] }>(path(`/notifiers/${n.id}/test`), { method: 'POST' }))
  testing.value = null
  if (res) testResults.value = res.results
}

async function remove(n: any) {
  if (!(await confirm.confirm({ message: t('notifiers.remove_confirm', { name: n.name }), tone: 'danger', confirmLabel: t('common.delete') }))) return
  const res = await run(() => api(path(`/notifiers/${n.id}`), { method: 'DELETE' }), t('notifiers.removed'))
  if (res) load()
}

onMounted(load)
</script>
