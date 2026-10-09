<template>
  <aside class="flex flex-col min-w-0 h-full bg-white dark:bg-slate-800 border-r border-slate-200 dark:border-slate-700">
    <!-- the conversations of a tenant in three queues: the ones that wait for a person,
         the ones the bot leads itself, and all of them -->
    <div class="shrink-0 px-4 pt-4 pb-3 space-y-3 border-b border-slate-200 dark:border-slate-700">
      <div class="flex items-center justify-between gap-2">
        <h1 class="text-xl font-bold text-slate-900 dark:text-white">{{ t('nav.inbox') }}</h1>
        <button
          type="button"
          class="btn-icon btn-ghost btn-sm"
          :class="filtersOpen || filtered ? 'text-primary-dark dark:text-primary-light' : ''"
          :aria-label="t('inbox.filters')"
          :aria-expanded="filtersOpen"
          :title="t('inbox.filters')"
          @click="filtersOpen = !filtersOpen"
        >
          <UiIcon name="search" :size="18" />
        </button>
      </div>

      <div class="segmented !flex !flex-nowrap w-full" role="tablist" :aria-label="t('nav.inbox')">
        <button
          v-for="q in QUEUES"
          :key="q"
          type="button"
          role="tab"
          class="segment flex-1 !px-2 whitespace-nowrap"
          :class="queue === q ? 'segment-active' : ''"
          :aria-selected="queue === q"
          @click="emit('update:queue', q)"
        >
          {{ t(`inbox.queue.${q}`) }}<template v-if="q !== 'all' && counts"> · {{ counts[q] }}</template>
        </button>
      </div>

      <div v-if="filtersOpen" class="space-y-2">
        <label class="sr-only" for="inbox-search">{{ t('common.search') }}</label>
        <input
          id="inbox-search"
          :value="search"
          type="search"
          class="input"
          :placeholder="t('chat.search_placeholder')"
          @input="emit('update:search', ($event.target as HTMLInputElement).value)"
        />
        <label class="sr-only" for="inbox-channel">{{ t('inbox.side.channel') }}</label>
        <select
          v-if="channels.length > 1"
          id="inbox-channel"
          :value="channelId"
          class="select"
          @change="emit('update:channelId', ($event.target as HTMLSelectElement).value)"
        >
          <option value="">{{ t('inbox.all_channels') }}</option>
          <option v-for="c in channels" :key="c.id" :value="c.id">{{ c.title }}</option>
        </select>
        <label class="flex items-center gap-2 min-h-[44px] text-sm">
          <input type="checkbox" class="checkbox" :checked="sandbox" @change="emit('update:sandbox', ($event.target as HTMLInputElement).checked)" />
          {{ t('inbox.sandbox_only') }}
        </label>
      </div>
    </div>

    <div class="flex-1 min-h-0 overflow-y-auto">
      <p v-if="error" class="alert-red m-4">{{ error }}</p>

      <ul v-if="items.length">
        <li v-for="c in items" :key="c.id">
          <NuxtLink
            :to="`${base}/inbox/${c.id}`"
            class="flex gap-3 px-4 py-3 border-b border-slate-200 dark:border-slate-700 border-l-4 transition-colors focus:outline-none focus-visible:bg-slate-50 dark:focus-visible:bg-slate-700/50"
            :class="c.id === selected
              ? 'bg-[#e8f2fc] dark:bg-sky-500/15 border-l-primary-dark'
              : 'border-l-transparent hover:bg-slate-50 dark:hover:bg-slate-700/40'"
            :aria-current="c.id === selected ? 'true' : undefined"
          >
            <span class="grid place-items-center w-10 h-10 shrink-0 rounded-full bg-[#e6e9f2] dark:bg-slate-700 font-semibold text-slate-900 dark:text-white">
              {{ initial(contactName(c.contact)) }}
            </span>
            <span class="min-w-0 flex-1">
              <span class="flex items-baseline justify-between gap-2">
                <span class="truncate text-slate-900 dark:text-white" :class="c.waiting ? 'font-bold' : 'font-semibold'">{{ contactName(c.contact) }}</span>
                <span class="shrink-0 text-[13px] muted">{{ f.relative(c.last_message?.created_at || c.updated_at) }}</span>
              </span>
              <span class="block truncate text-sm text-slate-600 dark:text-slate-300">{{ preview(c) }}</span>
              <span class="mt-1.5 flex flex-wrap items-center gap-1.5">
                <span v-for="tag in tags(c)" :key="tag.key" :class="tag.cls">{{ t(`inbox.tag.${tag.key}`) }}</span>
                <span v-if="channels.length > 1" class="text-[13px] muted truncate">{{ c.channel_title }}</span>
              </span>
            </span>
          </NuxtLink>
        </li>
      </ul>

      <!-- nothing in the queue: say what this queue is and how conversations get here -->
      <div v-else-if="loaded" class="px-5 py-8 text-center">
        <template v-if="search || channelId">
          <p class="font-semibold text-slate-900 dark:text-white">{{ t('inbox.empty.search') }}</p>
        </template>
        <template v-else-if="queue === 'waiting'">
          <span class="grid place-items-center w-12 h-12 mx-auto rounded-full bg-[#e4f3ea] text-[#17663f] dark:bg-emerald-500/15 dark:text-emerald-300">
            <UiIcon name="check" :size="22" :stroke="2.5" />
          </span>
          <p class="mt-3 font-semibold text-slate-900 dark:text-white">{{ t('inbox.empty.waiting') }}</p>
          <p class="mt-1 text-sm muted">{{ t('inbox.empty.waiting_sub') }}</p>
          <div class="mt-5 rounded-card border border-slate-200 dark:border-slate-700 p-4 text-left text-sm">
            <p class="font-semibold text-slate-900 dark:text-white">{{ t('inbox.howto.title') }}</p>
            <ol class="mt-2 space-y-1.5 list-decimal pl-5 text-slate-600 dark:text-slate-300">
              <li>{{ t('inbox.howto.one') }}</li>
              <li>{{ t('inbox.howto.all') }}</li>
            </ol>
            <NuxtLink v-if="can('admin')" :to="`${base}/channels`" class="link inline-block mt-3">{{ t('inbox.howto.channels') }}</NuxtLink>
          </div>
        </template>
        <template v-else>
          <p class="font-semibold text-slate-900 dark:text-white">{{ t(sandbox ? 'inbox.empty.sandbox' : 'inbox.empty.none') }}</p>
          <p v-if="!sandbox" class="mt-1 text-sm muted">{{ t('inbox.empty.none_sub') }}</p>
        </template>
      </div>
      <p v-else class="px-5 py-8 text-center muted">{{ t('inbox.loading') }}</p>

      <div v-if="items.length < total" class="p-3">
        <button type="button" class="btn-secondary w-full" :disabled="loading" @click="emit('more')">
          {{ t('inbox.more', { n: total - items.length }) }}
        </button>
      </div>
    </div>

    <!-- the bot's own conversations are one tap away from the «waiting» queue -->
    <p v-if="queue === 'waiting' && counts?.bot" class="shrink-0 px-4 py-3 border-t border-slate-200 dark:border-slate-700 text-sm muted">
      {{ t('inbox.bot_rest', { n: counts.bot }) }}
      <button type="button" class="link" @click="emit('update:queue', 'bot')">{{ t('inbox.show') }}</button>
    </p>
  </aside>
</template>

<script setup lang="ts">
import { contactName, initial } from '~/utils/texts'

type Queue = 'waiting' | 'bot' | 'all'
const QUEUES: Queue[] = ['waiting', 'bot', 'all']

const props = defineProps<{
  items: any[]
  total: number
  counts: { waiting: number; bot: number; all: number } | null
  queue: Queue
  search: string
  channelId: string
  sandbox: boolean
  selected?: string | null
  loading?: boolean
  loaded?: boolean
  error?: string | null
}>()
const emit = defineEmits<{
  'update:queue': [Queue]
  'update:search': [string]
  'update:channelId': [string]
  'update:sandbox': [boolean]
  more: []
}>()
const { t } = useI18n()
const f = useFormat()
const { base, can } = useTenant()
const { channels } = useTenantInfo()

const filtered = computed(() => !!props.search || !!props.channelId || props.sandbox)
const filtersOpen = ref(filtered.value)

// the last message in one line; a message without text is an attachment
function preview(c: any): string {
  const m = c.last_message
  if (!m) return t('inbox.no_messages')
  const text = (m.text || '').trim() || t('chat.attachment')
  return m.role === 'client' ? text : `${t(m.role === 'bot' ? 'inbox.preview.bot' : 'inbox.preview.you')}: ${text}`
}

// why the conversation is in the list, in the person's words
function tags(c: any): { key: string; cls: string }[] {
  const out: { key: string; cls: string }[] = []
  const kinds: string[] = c.open_tasks || []
  if (kinds.includes('choose_reply')) out.push({ key: 'choose_reply', cls: 'badge-violet' })
  if (kinds.includes('review_receipt')) out.push({ key: 'review_receipt', cls: 'badge-amber' })
  if (kinds.includes('approve_action')) out.push({ key: 'approve_action', cls: 'badge-amber' })
  if (c.handover) out.push(c.waiting ? { key: 'needs_person', cls: 'badge-red' } : { key: 'yours', cls: 'badge-gray' })
  else if (c.operator_mode && !out.length) out.push({ key: 'suggest', cls: 'badge-gray' })
  if (c.sandbox) out.push({ key: 'sandbox', cls: 'badge-gray' })
  return out
}
</script>
