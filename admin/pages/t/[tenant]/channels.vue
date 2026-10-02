<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="page-title">{{ t('nav.channels') }}</h1>
        <p class="page-sub">{{ t('channels.sub') }}</p>
      </div>
      <div v-if="can('admin')" class="flex flex-wrap gap-2">
        <button type="button" class="btn-secondary" :disabled="!ig?.configured" @click="tokenOpen = true">
          {{ t('instagram.add_token') }}
        </button>
        <button type="button" class="btn-primary" :disabled="!ig?.configured || connecting" @click="connect">
          <UiIcon name="plus" :size="16" /> {{ t('instagram.connect') }}
        </button>
      </div>
    </div>

    <p v-if="returnError" class="alert-red">{{ returnError }}</p>

    <UiState :loading="loading" :error="error" :has-data="!!info" :empty="info && !channels.length ? t('channels.empty') : false" :retry="() => load(true)">
      <div class="grid gap-5 lg:grid-cols-4">
        <div class="card p-2 h-fit">
          <button
            v-for="c in channels"
            :key="c.id"
            type="button"
            class="w-full flex items-center gap-2.5 rounded-button px-3 py-2.5 text-left"
            :class="c.id === selectedId ? 'bg-primary/10 text-primary' : 'hover:bg-slate-50 dark:hover:bg-slate-700/40'"
            @click="selectedId = c.id"
          >
            <img v-if="c.avatar_url" :src="c.avatar_url" alt="" class="w-8 h-8 rounded-full object-cover" referrerpolicy="no-referrer" />
            <span v-else class="grid place-items-center w-8 h-8 rounded-full bg-slate-100 dark:bg-slate-700 text-xs font-semibold uppercase">
              {{ c.kind === 'instagram' ? 'IG' : 'API' }}
            </span>
            <span class="flex-1 min-w-0">
              <span class="block truncate font-medium">{{ c.title }}</span>
              <span class="block truncate text-xs muted">{{ c.username ? `@${c.username}` : t(`channels.kind.${c.kind}`) }}</span>
            </span>
            <StatusBadge kind="channel" :value="c.status" />
          </button>
        </div>

        <div v-if="selected" class="lg:col-span-3 card">
          <div class="card-body pb-0">
            <div class="flex flex-wrap items-center gap-2">
              <h2 class="card-title">{{ selected.title }}</h2>
              <StatusBadge kind="channel" :value="selected.status" />
              <span class="badge-gray">{{ t(`channels.kind.${selected.kind}`) }}</span>
              <button
                v-if="selected.kind === 'instagram' && selected.has_token && can('admin')"
                type="button"
                class="ml-auto btn-secondary btn-sm text-rose-600"
                :disabled="busy"
                @click="disconnect"
              >
                {{ t('instagram.disconnect') }}
              </button>
            </div>
            <p v-if="selected.status_error" class="alert-red mt-3">{{ codeText({ code: selected.status_error }) }}</p>
            <p v-if="selected.kind === 'instagram' && !selected.has_token" class="alert-amber mt-3">{{ t('instagram.disconnected_hint') }}</p>
            <p v-if="selected.token_expires_at" class="text-xs muted mt-2">{{ t('channels.token_expires', { at: f.dateTime(selected.token_expires_at) }) }}</p>
            <div class="tabs mt-4">
              <button type="button" class="tab" :class="tab === 'scenarios' ? 'tab-active' : ''" @click="tab = 'scenarios'">{{ t('nav.scenarios') }}</button>
              <button type="button" class="tab" :class="tab === 'settings' ? 'tab-active' : ''" @click="tab = 'settings'">{{ t('nav.settings') }}</button>
            </div>
          </div>
          <div class="card-body">
            <ChannelScenarios v-if="tab === 'scenarios'" :key="selected.id" :channel-id="selected.id" :can-edit="can('admin')" />
            <ChannelSettings
              v-else
              :channel="selected"
              :tenant-settings="info.effective_settings"
              :can-edit="can('admin')"
              @saved="load(true)"
            />
          </div>
        </div>
      </div>
    </UiState>

    <!-- what to enter in the Meta app dashboard (one app for every account) -->
    <div v-if="can('admin') && ig" class="card card-body space-y-3 text-sm">
      <div class="flex flex-wrap items-center gap-2">
        <h2 class="card-title">{{ t('instagram.setup_title') }}</h2>
        <span :class="ig.configured ? 'badge-green' : 'badge-amber'">{{ ig.configured ? t('instagram.configured') : t('instagram.not_configured') }}</span>
      </div>
      <p class="muted">{{ t('instagram.setup_text') }}</p>
      <Row :label="t('instagram.webhook_url')">
        <div class="flex flex-wrap items-center gap-2">
          <span class="code break-all">{{ ig.webhook_url }}</span>
          <CopyButton :value="ig.webhook_url" />
        </div>
      </Row>
      <Row :label="t('instagram.verify_token')">
        <span :class="ig.verify_token_set ? 'badge-green' : 'badge-amber'">{{ ig.verify_token_set ? t('instagram.set') : t('instagram.not_set') }}</span>
      </Row>
      <Row :label="t('instagram.redirect_uri')">
        <div class="flex flex-wrap items-center gap-2">
          <span class="code break-all">{{ ig.redirect_uri }}</span>
          <CopyButton :value="ig.redirect_uri" />
        </div>
      </Row>
      <Row :label="t('instagram.human_agent')">{{ ig.human_agent_tag ? t('instagram.on') : t('instagram.off') }}</Row>
      <p class="hint">{{ t('instagram.review_hint') }}</p>
    </div>

    <UiModal :open="tokenOpen" :title="t('instagram.add_token')" @close="tokenOpen = false">
      <form id="ig-token-form" class="space-y-3" @submit.prevent="addToken">
        <p class="text-sm muted">{{ t('instagram.token_hint') }}</p>
        <textarea v-model.trim="token" rows="4" class="textarea code" required minlength="20" autocomplete="off" />
        <p v-if="tokenError" class="alert-red">{{ tokenError }}</p>
      </form>
      <template #footer>
        <button type="button" class="btn-secondary" @click="tokenOpen = false">{{ t('common.cancel') }}</button>
        <button type="submit" form="ig-token-form" class="btn-primary" :disabled="busy">{{ t('instagram.check_and_add') }}</button>
      </template>
    </UiModal>
  </div>
</template>

<script setup lang="ts">
const { t } = useI18n()
const f = useFormat()
const api = useApi()
const route = useRoute()
const router = useRouter()
const toast = useToast()
const confirm = useConfirm()
const { codeText, errorText } = useErrorText()
const { can, path } = useTenant()
const { info, channels, load: loadInfo } = useTenantInfo()
const { busy, run } = useAction()
usePage(() => [{ label: t('nav.channels') }])

const selectedId = ref('')
const tab = ref<'scenarios' | 'settings'>('scenarios')
const selected = computed(() => channels.value.find((c) => c.id === selectedId.value) || null)
const ig = ref<any>(null)
const connecting = ref(false)
const returnError = ref<string | null>(null)
const tokenOpen = ref(false)
const token = ref('')
const tokenError = ref<string | null>(null)

const { loading, error, load: loadOnce } = useLoader(() => loadInfo(true))
async function load(force = false) {
  if (force || !info.value) await loadOnce()
  if (!selected.value) selectedId.value = channels.value[0]?.id || ''
}

async function connect() {
  connecting.value = true
  const res = await run(() => api<{ url: string }>(path('/channels/instagram/connect')))
  if (res?.url) window.location.href = res.url
  else connecting.value = false
}

async function addToken() {
  busy.value = true
  tokenError.value = null
  try {
    const res = await api<any>(path('/channels/instagram/token'), { method: 'POST', body: { token: token.value } })
    tokenOpen.value = false
    token.value = ''
    toast.ok(t(res.created ? 'instagram.connected' : 'instagram.updated', { name: res.channel.title }))
    await load(true)
    selectedId.value = res.channel.id
  } catch (e) {
    tokenError.value = errorText(e)
  } finally {
    busy.value = false
  }
}

async function disconnect() {
  const c = selected.value
  if (!c) return
  if (!(await confirm.confirm({ message: t('instagram.disconnect_confirm', { name: c.title }), tone: 'danger', confirmLabel: t('instagram.disconnect') }))) return
  const res = await run(() => api(path(`/channels/${c.id}/disconnect`), { method: 'POST' }), t('instagram.disconnected'))
  if (res) load(true)
}

onMounted(async () => {
  // back from the Instagram login: ?connected=<channel id> or ?error=<code>
  const { connected, error: code } = route.query
  if (connected || code) router.replace({ query: {} })
  if (typeof code === 'string') returnError.value = codeText({ code })
  await load(true)
  if (typeof connected === 'string') {
    selectedId.value = connected
    toast.ok(t('instagram.connected', { name: selected.value?.title || '' }))
  }
  if (can('admin')) {
    try {
      ig.value = await api('/channels/instagram/info')
    } catch {
      ig.value = null
    }
  }
})
</script>
