<template>
  <section class="flex flex-col min-w-0 h-full bg-slate-50 dark:bg-slate-900">
    <!-- one conversation: who answers (bot / bot suggests / only me), the messages, and
         what a person can do right now -->
    <!-- header -->
    <header class="shrink-0 bg-white dark:bg-slate-800 border-b border-slate-200 dark:border-slate-700 px-4 py-3">
      <div class="flex flex-wrap items-center gap-3">
        <button type="button" class="btn-icon btn-ghost lg:hidden -ml-2" :aria-label="t('common.back')" @click="emit('back')">
          <UiIcon name="chevron-left" />
        </button>
        <span class="grid place-items-center w-10 h-10 shrink-0 rounded-full bg-[#e6e9f2] dark:bg-slate-700 font-semibold text-slate-900 dark:text-white">
          {{ initial(name) }}
        </span>
        <div class="min-w-0 flex-1">
          <h1 class="font-semibold text-slate-900 dark:text-white truncate">{{ name }}</h1>
          <p class="text-sm muted truncate">
            {{ detail.channel?.title }}
            <template v-if="conv.sandbox"> · {{ t('nav.sandbox') }}</template>
          </p>
        </div>
        <button type="button" class="btn-secondary btn-sm xl:hidden" @click="emit('side')">{{ t('inbox.about_client') }}</button>
      </div>

      <!-- who answers -->
      <div v-if="!conv.sandbox" class="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2">
        <span id="who-answers" class="text-sm font-semibold text-slate-900 dark:text-slate-200">{{ t('inbox.who.label') }}</span>
        <div class="segmented" role="radiogroup" aria-labelledby="who-answers">
          <button
            v-for="m in MODES"
            :key="m"
            type="button"
            role="radio"
            class="segment"
            :class="mode === m ? 'segment-active' : ''"
            :aria-checked="mode === m"
            :disabled="busy || (m === 'bot' && conv.channel_operator_mode)"
            :title="m === 'bot' && conv.channel_operator_mode ? t('inbox.who.channel_locked') : undefined"
            @click="setMode(m)"
          >
            {{ t(`inbox.who.${m}`) }}
          </button>
        </div>
      </div>
      <p v-if="!conv.sandbox" class="mt-1.5 text-sm muted">
        {{ t(`inbox.who.${mode}_hint`) }}
        <NuxtLink v-if="conv.channel_operator_mode && mode === 'suggest'" :to="`${base}/channels`" class="link">{{ t('inbox.who.channel_link') }}</NuxtLink>
      </p>
    </header>

    <!-- messages -->
    <div ref="scroller" class="flex-1 min-h-0 overflow-y-auto px-4 py-5">
      <div class="max-w-3xl mx-auto">
        <ChatThread :messages="detail.messages" :explainable="canExplain" @explain="turnOpen = $event" />
      </div>
    </div>

    <!-- what a person can do now -->
    <footer class="shrink-0 border-t border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-4 py-3 max-h-[70%] overflow-y-auto">
      <div class="max-w-3xl mx-auto space-y-3">
        <TaskCard v-for="task in otherTasks" :key="task.id" :task="task" can-act @resolved="emit('changed')" />

        <p v-if="conv.sandbox" class="text-sm muted">{{ t('inbox.sandbox_readonly') }}</p>

        <ReplyVariants
          v-else-if="variantsTask"
          :key="variantsTask.id"
          :task="variantsTask"
          :can-act="detail.operator_can_write"
          @resolved="emit('changed')"
        />

        <template v-else>
          <p v-if="!detail.operator_can_write" class="alert-amber">{{ t('operator.cannot_write') }}</p>
          <form v-else class="space-y-2" @submit.prevent="send">
            <label class="sr-only" for="inbox-reply">{{ t('operator.write') }}</label>
            <div class="flex items-end gap-2">
              <textarea
                id="inbox-reply"
                v-model="reply"
                rows="2"
                class="textarea flex-1"
                :placeholder="t(`inbox.compose.${mode}`)"
                @keydown.enter.ctrl.prevent="send"
                @keydown.enter.meta.prevent="send"
              />
              <button type="submit" class="btn-primary" :disabled="busy || !reply.trim()">
                <UiIcon name="send" :size="18" /> {{ t('inbox.send') }}
              </button>
            </div>
            <p class="text-[13px] muted">
              <template v-if="mode === 'me'">
                {{ t('inbox.compose.me_note') }}
                <button type="button" class="link" :disabled="busy" @click="setMode(conv.operator_mode || conv.channel_operator_mode ? 'suggest' : 'bot')">{{ t('operator.release') }}</button>
              </template>
              <template v-else-if="mode === 'suggest'">{{ t('inbox.compose.suggest_note') }}</template>
              <template v-else>{{ t('inbox.compose.bot_note') }}</template>
            </p>
          </form>
        </template>
      </div>
    </footer>

    <!-- why the bot answered this way, in plain words; the whole trace is inside -->
    <UiModal :open="!!turnOpen" :title="t('why.title')" size="lg" @close="turnOpen = null">
      <TurnPanel v-if="turnOpen" :key="turnOpen" :turn-id="turnOpen" plain :runs="detail.runs" />
    </UiModal>
  </section>
</template>

<script setup lang="ts">
import { contactName, initial } from '~/utils/texts'

type Mode = 'bot' | 'suggest' | 'me'
const MODES: Mode[] = ['bot', 'suggest', 'me']

const props = defineProps<{ detail: any }>()
const emit = defineEmits<{ changed: []; back: []; side: [] }>()
const { t } = useI18n()
const api = useApi()
const { base, path, can } = useTenant()
const { busy, run } = useAction()

const conv = computed(() => props.detail.conversation)
const name = computed(() => contactName(props.detail.contact))
const convPath = computed(() => path(`/conversations/${conv.value.id}`))
// the raw trace of a turn is for the people who set the bot up
const canExplain = computed(() => can('admin'))

// handover: the bot is silent; operator mode (of the conversation or of its whole
// channel): the bot only suggests; otherwise it answers itself
const mode = computed<Mode>(() =>
  conv.value.handover ? 'me' : conv.value.operator_mode || conv.value.channel_operator_mode ? 'suggest' : 'bot',
)

const variantsTask = computed(() => (props.detail.tasks || []).find((x: any) => x.kind === 'choose_reply') || null)
// the handover itself is shown by the switch above, not as a card
const otherTasks = computed(() => (props.detail.tasks || []).filter((x: any) => x.kind !== 'choose_reply' && x.kind !== 'handover'))

const reply = ref('')
const turnOpen = ref<string | null>(null)

async function setMode(target: Mode) {
  if (target === mode.value || busy.value) return
  const c = conv.value
  await run(async () => {
    if (target === 'me') {
      await api(`${convPath.value}/handover`, { method: 'POST' })
      return
    }
    if (c.handover) await api(`${convPath.value}/release`, { method: 'POST' })
    const wantSuggest = target === 'suggest'
    if (c.operator_mode !== wantSuggest && !(wantSuggest && c.channel_operator_mode)) {
      await api(convPath.value, { method: 'PATCH', body: { operator_mode: wantSuggest } })
    }
  }, t(`inbox.who.${target}_done`))
  emit('changed')
}

async function send() {
  const text = reply.value.trim()
  if (!text || busy.value) return
  const res = await run(() => api(`${convPath.value}/messages`, { method: 'POST', body: { text } }), t('operator.sent'))
  if (res) reply.value = ''
  emit('changed')
}

// keep the newest message in view: on opening and when a new one arrives
const scroller = ref<HTMLElement | null>(null)
function toBottom() {
  nextTick(() => {
    if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
  })
}
watch(
  () => [conv.value.id, props.detail.messages.length],
  (now, before) => {
    if (!before || now[0] !== before[0]) reply.value = ''
    toBottom()
  },
  { immediate: true },
)
onMounted(toBottom)
</script>
