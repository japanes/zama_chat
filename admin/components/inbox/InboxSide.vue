<template>
  <div class="space-y-4">
    <!-- next to the chat: what the bot has collected and who the client is -->
    <div class="card card-body">
      <h2 class="card-title mb-3">{{ t('inbox.side.progress') }}</h2>
      <template v-if="shownRuns.length">
        <div v-for="(r, i) in shownRuns" :key="r.id" :class="i ? 'mt-4 pt-4 border-t border-slate-200 dark:border-slate-700' : ''">
          <RunProgress :run="r" />
          <NuxtLink :to="`${base}/results/${r.id}`" class="link inline-block mt-3 text-sm">{{ t('inbox.side.open_request') }}</NuxtLink>
        </div>
        <button v-if="olderRuns.length && !showOlder" type="button" class="link mt-3 text-sm" @click="showOlder = true">
          {{ t('inbox.side.older', { n: olderRuns.length }) }}
        </button>
      </template>
      <p v-else class="text-sm muted">{{ t('inbox.side.no_scenario') }}</p>
    </div>

    <div class="card card-body">
      <h2 class="card-title mb-3">{{ t('chat.client') }}</h2>
      <dl class="space-y-2.5 text-sm">
        <div>
          <dt class="muted">{{ t('inbox.side.name') }}</dt>
          <dd class="font-medium text-slate-900 dark:text-white break-words">{{ name }}</dd>
        </div>
        <div v-if="detail.contact?.username">
          <dt class="muted">{{ t('chat.username') }}</dt>
          <dd class="break-all">@{{ detail.contact.username }}</dd>
        </div>
        <div>
          <dt class="muted">{{ t('inbox.side.channel') }}</dt>
          <dd>{{ detail.channel?.title || '—' }}</dd>
        </div>
        <div v-if="conv.client_language">
          <dt class="muted">{{ t('inbox.side.language') }}</dt>
          <dd>{{ t('chat.languages', { client: conv.client_language, reply: conv.reply_language || '—' }) }}</dd>
        </div>
        <div>
          <dt class="muted">{{ t('chat.last_client') }}</dt>
          <dd>{{ f.dateTime(conv.last_client_at) }}</dd>
        </div>
        <div>
          <dt class="muted">{{ t('inbox.side.first_seen') }}</dt>
          <dd>{{ f.dateTime(conv.created_at) }}</dd>
        </div>
      </dl>

      <!-- Instagram lets a business answer only for 24 hours after the client's message -->
      <p v-if="conv.window_closes_at" class="mt-4" :class="windowOpen ? 'alert-blue' : 'alert-amber'">
        <template v-if="windowOpen">
          <span class="font-semibold">{{ t('inbox.side.window_until', { at: f.dateTime(conv.window_closes_at) }) }}</span>
          <span class="block mt-0.5 text-[13px]">{{ t('inbox.side.window_why') }}</span>
        </template>
        <template v-else>{{ t('inbox.side.window_closed') }}</template>
      </p>

      <details class="mt-4 text-sm">
        <summary class="cursor-pointer muted">{{ t('inbox.side.technical') }}</summary>
        <p class="mt-2 muted">{{ t('chat.external_id') }}</p>
        <p class="code text-xs break-all">{{ detail.contact?.external_id }}</p>
      </details>
    </div>
  </div>
</template>

<script setup lang="ts">
import { contactName } from '~/utils/texts'

const props = defineProps<{ detail: any }>()
const { t } = useI18n()
const f = useFormat()
const { base } = useTenant()

const OPEN = ['focus', 'paused', 'waiting']
const conv = computed(() => props.detail.conversation)
const name = computed(() => contactName(props.detail.contact))
const showOlder = ref(false)

// the scenarios still going on; without any — the last finished one
const openRuns = computed(() => (props.detail.runs || []).filter((r: any) => OPEN.includes(r.status)))
const latest = computed(() => (openRuns.value.length ? openRuns.value : (props.detail.runs || []).slice(0, 1)))
const olderRuns = computed(() => (props.detail.runs || []).filter((r: any) => !latest.value.includes(r)))
const shownRuns = computed(() => (showOlder.value ? [...latest.value, ...olderRuns.value] : latest.value))

const windowOpen = computed(() => {
  const at = conv.value.window_closes_at
  return !at || new Date(at).getTime() > Date.now()
})

watch(
  () => conv.value.id,
  () => (showOlder.value = false),
)
</script>
