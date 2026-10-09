<template>
  <div class="space-y-5">
    <!-- step 4: when the request is ready, what the tenant's system may answer, and what
         happens on the way (send the request, notify, write to the client, call a person) -->
    <fieldset class="space-y-1.5">
      <legend class="label">{{ t('editor.next.when') }}</legend>
      <label
        v-for="m in MODES"
        :key="m"
        class="flex items-start gap-2.5 rounded-button border px-3.5 py-2.5 cursor-pointer"
        :class="mode === m ? 'border-primary-dark bg-[#e8f2fc] dark:border-primary-light dark:bg-sky-500/10' : 'border-slate-200 dark:border-slate-700'"
      >
        <input type="radio" class="mt-1 accent-primary-dark" :name="`${uid}-mode`" :value="m" :checked="mode === m" :disabled="readonly" @change="setMode(m)" />
        <span>
          <span class="block font-medium text-slate-900 dark:text-white">{{ t(`editor.next.mode.${m}`) }}</span>
          <span class="block text-sm muted">{{ t(`editor.next.mode_hint.${m}`) }}</span>
        </span>
      </label>
    </fieldset>

    <!-- the answers of the outside system -->
    <div v-if="mode === 'external_event'" class="space-y-2.5">
      <div>
        <p class="label !mb-0.5">{{ t('editor.next.events') }}</p>
        <p class="text-sm muted">{{ t('editor.next.events_hint') }}</p>
      </div>
      <div v-for="(e, i) in (def.events_list as any[])" :key="i" class="rounded-button border border-slate-200 dark:border-slate-700 p-3 space-y-3">
        <div class="flex flex-wrap items-end gap-2.5">
          <div class="flex-1 basis-[150px] min-w-0">
            <label class="label" :for="`${uid}-ename-${i}`">{{ t('editor.next.event_code') }}</label>
            <input :id="`${uid}-ename-${i}`" v-model.trim="e.name" class="input code" placeholder="booked" :readonly="readonly" />
          </div>
          <div class="flex-1 basis-[220px] min-w-0">
            <label class="label" :for="`${uid}-eres-${i}`">{{ t('editor.next.event_means') }}</label>
            <select :id="`${uid}-eres-${i}`" v-model="e.result" class="select" :disabled="readonly">
              <option v-for="r in EVENT_RESULTS" :key="r" :value="r">{{ t(`editor.next.event_result.${r}`) }}</option>
            </select>
          </div>
          <button v-if="!readonly" type="button" class="btn-icon btn-ghost" :aria-label="t('common.delete')" @click="def.events_list.splice(i, 1)">
            <UiIcon name="close" :size="18" />
          </button>
        </div>
        <div v-if="e.result === 'reopen'">
          <p class="label">{{ t('editor.next.reopen') }}</p>
          <div class="flex flex-wrap gap-1.5">
            <button
              v-for="f in fields"
              :key="f.key"
              type="button"
              class="chip"
              :class="e.reopen_fields.includes(f.key) ? 'chip-on' : ''"
              :aria-pressed="e.reopen_fields.includes(f.key)"
              :disabled="readonly"
              @click="toggleIn(e.reopen_fields, f.key)"
            >
              {{ name(f) }}
            </button>
          </div>
        </div>
        <div>
          <label class="label" :for="`${uid}-esay-${i}`">{{ t('editor.next.event_say') }} <span class="muted font-normal">({{ t('common.optional') }})</span></label>
          <LocalizedInput v-model="e.say" :input-id="`${uid}-esay-${i}`" :languages="languages" :main="def.language" multiline optional :readonly="readonly" />
          <p v-if="fields.length" class="hint">
            {{ t('editor.next.say_hint') }}
            <span v-for="f in fields.slice(0, 4)" :key="f.key" class="code">{{ slot(f.key) }}</span>
          </p>
        </div>
      </div>
      <button v-if="!readonly" type="button" class="btn-dashed" @click="addEvent">
        <UiIcon name="plus" :size="16" /> {{ t('editor.next.add_event') }}
      </button>
      <label class="flex flex-wrap items-center gap-2">
        <span>{{ t('editor.next.wait') }}</span>
        <input v-model="def.waiting.hours" type="number" min="0.1" step="0.5" class="input !w-24" :placeholder="'∞'" :readonly="readonly" :aria-label="t('editor.next.wait_label')" />
        <span>{{ t('editor.hours') }}</span>
      </label>
      <p class="hint !mt-0">{{ t('editor.next.wait_hint') }}</p>
      <details class="text-sm">
        <summary class="cursor-pointer muted">{{ t('editor.next.events_dev') }}</summary>
        <p class="mt-1 text-slate-600 dark:text-slate-300">{{ t('scenarios.events_hint') }}</p>
      </details>
    </div>

    <!-- actions -->
    <div class="space-y-2.5">
      <div>
        <p class="label !mb-0.5">{{ t('editor.next.actions') }}</p>
        <p class="text-sm muted">{{ t('editor.next.actions_hint') }}</p>
      </div>
      <p v-if="!def.actions.length" class="alert-amber">{{ t('editor.next.no_actions') }}</p>
      <div v-for="(a, i) in (def.actions as any[])" :key="i" class="rounded-button border border-slate-200 dark:border-slate-700 p-3 space-y-3">
        <div class="flex flex-wrap items-end gap-2.5">
          <div class="flex-1 basis-[190px] min-w-0">
            <label class="label" :for="`${uid}-on-${i}`">{{ t('editor.next.action_on') }}</label>
            <select :id="`${uid}-on-${i}`" v-model="a.on" class="select" :disabled="readonly">
              <option v-for="e in ACTION_EVENTS" :key="e" :value="e">{{ t(`editor.next.on.${e}`) }}</option>
            </select>
          </div>
          <div class="flex-1 basis-[190px] min-w-0">
            <label class="label" :for="`${uid}-do-${i}`">{{ t('editor.next.action_do') }}</label>
            <select :id="`${uid}-do-${i}`" v-model="a.do" class="select" :disabled="readonly">
              <option v-for="d in ACTIONS" :key="d" :value="d">{{ t(`editor.next.do.${d}`) }}</option>
            </select>
          </div>
          <div v-if="a.do === 'http_post'" class="flex-1 basis-[170px] min-w-0">
            <label class="label" :for="`${uid}-to-${i}`">{{ t('editor.next.action_to') }}</label>
            <select v-if="endpointChoices(a.endpoint).length" :id="`${uid}-to-${i}`" v-model="a.endpoint" class="select" :disabled="readonly">
              <option value="">—</option>
              <option v-for="e in endpointChoices(a.endpoint)" :key="e" :value="e">{{ e }}</option>
            </select>
            <NuxtLink v-else :to="`${base}/endpoints`" class="link flex items-center min-h-[44px]">{{ t('editor.next.no_endpoints') }}</NuxtLink>
          </div>
          <div v-if="NOTIFY_ACTIONS.includes(a.do)" class="flex-1 basis-[170px] min-w-0">
            <label class="label" :for="`${uid}-to-${i}`">{{ t('editor.next.action_to') }}</label>
            <select v-if="notifierChoices(a).length" :id="`${uid}-to-${i}`" v-model="a.params.notifier" class="select" :disabled="readonly">
              <option value="">—</option>
              <option v-for="n in notifierChoices(a)" :key="n" :value="n">{{ n }}</option>
            </select>
            <NuxtLink v-else :to="`${base}/notifiers`" class="link flex items-center min-h-[44px]">{{ t('editor.next.no_notifiers') }}</NuxtLink>
          </div>
          <button v-if="!readonly" type="button" class="btn-icon btn-ghost" :aria-label="t('common.delete')" @click="def.actions.splice(i, 1)">
            <UiIcon name="close" :size="18" />
          </button>
        </div>

        <div v-if="a.do === 'message_client'">
          <label class="label" :for="`${uid}-text-${i}`">{{ t('scenarios.message_text') }}</label>
          <LocalizedInput v-model="a.params.text" :input-id="`${uid}-text-${i}`" :languages="languages" :main="def.language" multiline :readonly="readonly" />
          <p class="hint">{{ t('scenarios.message_text_hint') }}</p>
        </div>
        <p v-if="a.do === 'handover'" class="text-sm muted">{{ t('editor.next.handover_hint') }}</p>

        <div class="flex flex-wrap items-center gap-x-5 gap-y-2">
          <label v-if="a.on === 'silence'" class="flex items-center gap-2">
            {{ t('editor.next.silence_after') }}
            <input v-model.number="a.after_minutes" type="number" min="1" class="input !h-9 !w-24" :placeholder="t('scenarios.default')" :readonly="readonly" />
            {{ t('editor.minutes') }}
          </label>
          <label v-if="a.on === 'field_filled' || a.on === 'field_stuck'" class="flex items-center gap-2">
            {{ t('editor.next.which_question') }}
            <select v-model="a.field" class="select !h-9 !w-auto" :disabled="readonly">
              <option :value="undefined">{{ t('editor.next.any_question') }}</option>
              <option v-for="f in fields" :key="f.key" :value="f.key">{{ name(f) }}</option>
            </select>
          </label>
          <label class="flex items-center gap-2 min-h-[36px]">
            <input v-model="a.requires_approval" type="checkbox" class="checkbox" :disabled="readonly" />
            {{ t('editor.next.approval') }}
          </label>
          <label v-if="ctx?.showKeys.value && a.do === 'http_post'" class="flex items-center gap-2">
            {{ t('scenarios.action_status') }}
            <input v-model.trim="a.status" class="input !h-9 !w-36 code" :placeholder="t('scenarios.default')" :readonly="readonly" />
          </label>
        </div>
      </div>
      <button v-if="!readonly" type="button" class="btn-dashed" @click="addAction">
        <UiIcon name="plus" :size="16" /> {{ t('editor.next.add_action') }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ACTIONS, ACTION_EVENTS, EVENT_RESULTS, NOTIFY_ACTIONS, fieldName } from '~/utils/scenario'

const MODES = ['all_fields', 'external_event', 'receipt'] as const
type Mode = (typeof MODES)[number]

const props = defineProps<{
  def: any
  languages: string[]
  endpoints: string[]
  // [{name, kind}] of the tenant's notifiers
  notifiers?: { name: string; kind: string }[]
  readonly?: boolean
}>()
const { t } = useI18n()
const { base } = useTenant()
const ctx = useEditorContext()
const uid = useId()

const fields = computed(() => (props.def.fields as any[]).filter((f) => f.key))
// «the client's yes» lives in step 3, so here the two modes are one
const mode = computed<Mode>(() => (props.def.completion === 'explicit_yes' ? 'all_fields' : props.def.completion || 'all_fields'))

function name(f: any): string {
  return fieldName(f, ctx?.lang.value, props.def.language) || t('editor.q.untitled')
}

// "{service}": the client's answer inside the bot's message
function slot(key: string): string {
  return ` {${key}}`
}

function setMode(m: Mode) {
  if (m === mode.value) return
  props.def.completion = m
  if (m === 'external_event' && !props.def.events_list.length) addEvent()
}

function addEvent() {
  const list = props.def.events_list as any[]
  list.push({ name: list.length ? `event_${list.length + 1}` : 'booked', result: 'completed', reopen_fields: [], say: {} })
}

function addAction() {
  const waits = mode.value === 'external_event' && !(props.def.actions as any[]).some((a) => a.on === 'waiting')
  props.def.actions.push({ on: waits ? 'waiting' : 'completed', do: 'http_post', endpoint: props.endpoints[0] || '', params: {} })
}

function toggleIn(list: string[], k: string) {
  const i = list.indexOf(k)
  if (i >= 0) list.splice(i, 1)
  else list.push(k)
}

function endpointChoices(current?: string): string[] {
  return current && !props.endpoints.includes(current) ? [...props.endpoints, current] : props.endpoints
}
function notifierChoices(a: any): string[] {
  const kind = a.do === 'notify_email' ? 'email' : 'telegram'
  const names = (props.notifiers || []).filter((n) => n.kind === kind).map((n) => n.name)
  const current = a.params?.notifier
  return current && !names.includes(current) ? [...names, current] : names
}
</script>
