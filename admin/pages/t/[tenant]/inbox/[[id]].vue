<template>
  <div class="h-full flex min-w-0">
    <!-- Inbox: the list of conversations, the open one, and what the bot has collected.
         Fills the area under the header (page meta `fill`); every pane scrolls on its own. -->
    <InboxList
      v-model:queue="queue"
      v-model:search="search"
      v-model:channel-id="channelId"
      v-model:sandbox="sandbox"
      class="w-full lg:w-[340px] xl:w-[360px] shrink-0"
      :class="selectedId ? 'hidden lg:flex' : 'flex'"
      :items="items"
      :total="total"
      :counts="counts"
      :selected="selectedId"
      :loading="listLoading"
      :loaded="listLoaded"
      :error="listError"
      @more="more"
    />

    <div class="flex-1 min-w-0" :class="selectedId ? 'flex' : 'hidden lg:flex'">
      <template v-if="detail">
        <InboxChat :detail="detail" class="flex-1" @changed="refresh" @back="back" @side="sideOpen = true" />
        <div class="hidden xl:block w-[320px] shrink-0 overflow-y-auto border-l border-slate-200 dark:border-slate-700 p-4">
          <InboxSide :detail="detail" />
        </div>
      </template>
      <div v-else class="flex-1 grid place-items-center p-6 text-center">
        <div v-if="detailError" class="space-y-3">
          <p class="alert-red">{{ detailError }}</p>
          <button type="button" class="btn-secondary" @click="back">{{ t('inbox.to_list') }}</button>
        </div>
        <p v-else-if="selectedId" class="muted">{{ t('inbox.loading') }}</p>
        <div v-else class="max-w-sm">
          <p class="font-semibold text-slate-900 dark:text-white">{{ t('inbox.pick') }}</p>
          <p class="mt-1 text-sm muted">{{ t('inbox.pick_sub') }}</p>
        </div>
      </div>
    </div>

    <!-- narrower screens: the side pane opens over the chat -->
    <UiModal :open="sideOpen && !!detail" :title="t('inbox.about_client')" @close="sideOpen = false">
      <InboxSide v-if="detail" :detail="detail" />
    </UiModal>
  </div>
</template>

<script setup lang="ts">
type Queue = 'waiting' | 'bot' | 'all'

// one page for the list and for an open conversation: opening another one keeps the
// list, its scroll and the filters
definePageMeta({ key: (route) => `inbox-${route.params.tenant}`, fill: true })

const { t } = useI18n()
const api = useApi()
const route = useRoute()
const router = useRouter()
const { base, path, tenantId } = useTenant()
const { errorText } = useErrorText()
const { load: loadInfo } = useTenantInfo()
const waitingBadge = useInboxCount()
usePage(() => [{ label: t('nav.inbox') }])

const PAGE = 50
const MAX = 200
const POLL_MS = 5000

const queue = ref<Queue>((['waiting', 'bot', 'all'] as Queue[]).find((q) => q === route.query.queue) || 'waiting')
const search = ref('')
const channelId = ref('')
const sandbox = ref(route.query.sandbox === '1')
const limit = ref(PAGE)

const items = ref<any[]>([])
const total = ref(0)
const counts = ref<{ waiting: number; bot: number; all: number } | null>(null)
const listLoading = ref(false)
const listLoaded = ref(false)
const listError = ref<string | null>(null)

const selectedId = computed(() => (route.params.id as string | undefined) || null)
const detail = ref<any | null>(null)
const detailError = ref<string | null>(null)
const sideOpen = ref(false)
const isDesktop = useMediaQuery('(min-width: 1024px)')

// answers of an older request must not replace a newer list or another conversation
let listTicket = 0
let detailTicket = 0

async function loadList() {
  if (!tenantId.value) return
  const ticket = ++listTicket
  listLoading.value = true
  try {
    const query: Record<string, any> = { limit: limit.value, offset: 0, sandbox: sandbox.value }
    if (queue.value !== 'all') query.queue = queue.value
    if (search.value.trim()) query.q = search.value.trim()
    if (channelId.value) query.channel_id = channelId.value
    const res = await api<any>(path('/conversations'), { query })
    if (ticket !== listTicket) return
    items.value = res.items
    total.value = res.total
    counts.value = res.counts || null
    listError.value = null
    if (res.counts && !sandbox.value && !channelId.value) waitingBadge.value = res.counts.waiting
    pickFirst()
  } catch (e) {
    if (ticket === listTicket) listError.value = errorText(e)
  } finally {
    if (ticket === listTicket) {
      listLoading.value = false
      listLoaded.value = true
    }
  }
}

async function loadDetail() {
  const id = selectedId.value
  const ticket = ++detailTicket
  if (!id) {
    detail.value = null
    detailError.value = null
    return
  }
  try {
    const res = await api<any>(path(`/conversations/${id}`))
    if (ticket !== detailTicket) return
    detail.value = res
    detailError.value = null
  } catch (e) {
    if (ticket !== detailTicket) return
    // a quiet refresh that failed keeps what is on the screen
    if (detail.value?.conversation?.id !== id) {
      detail.value = null
      detailError.value = errorText(e)
    }
  }
}

function refresh() {
  loadDetail()
  loadList()
}

// a wide screen never shows an empty middle while there is something to open
function pickFirst() {
  if (!selectedId.value && isDesktop.value && items.value.length) {
    router.replace({ path: `${base.value}/inbox/${items.value[0].id}`, query: route.query })
  }
}

function back() {
  router.push({ path: `${base.value}/inbox`, query: route.query })
}

function more() {
  limit.value = Math.min(limit.value + PAGE, MAX)
  loadList()
}

watch(selectedId, (id) => {
  sideOpen.value = false
  if (detail.value?.conversation?.id !== id) detail.value = null
  detailError.value = null
  loadDetail()
})

// test conversations wait for nobody: show them all
watch(sandbox, (on) => {
  if (on) queue.value = 'all'
})
watch([queue, channelId, sandbox], () => {
  limit.value = PAGE
  listLoaded.value = false
  items.value = []
  router.replace({
    path: route.path,
    query: { ...(queue.value !== 'waiting' ? { queue: queue.value } : {}), ...(sandbox.value ? { sandbox: '1' } : {}) },
  })
  loadList()
})
watchDebounced(
  search,
  () => {
    limit.value = PAGE
    loadList()
  },
  { debounce: 300 },
)

// polling, no sockets: a quiet refresh while the tab is visible
const visibility = useDocumentVisibility()
useIntervalFn(() => {
  if (visibility.value !== 'visible') return
  if (!listLoading.value) loadList()
  loadDetail()
}, POLL_MS)

onMounted(() => {
  loadList()
  loadDetail()
  loadInfo().catch(() => {})
})
</script>
