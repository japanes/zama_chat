<template>
  <!-- everything about one turn: input → rewrite → route → scenarios → reply,
       every decision (Jev / LLM, confidence, cost) and every LLM call -->
  <div class="space-y-4 text-sm">
    <div class="flex flex-wrap items-center gap-2">
      <StatusBadge kind="turn" :value="turn.status" />
      <span class="muted">{{ f.dateTime(turn.created_at) }}</span>
      <span class="badge-gray">{{ f.ms(turn.duration_ms) }}</span>
      <span class="badge-gray">{{ f.money(turn.cost) }}</span>
      <span class="badge-gray">{{ t('trace.tokens', { in: turn.tokens_in ?? 0, out: turn.tokens_out ?? 0 }) }}</span>
      <span v-if="turn.client_language" class="badge-blue">{{ t('trace.languages', { client: turn.client_language, reply: turn.reply_language || '—' }) }}</span>
    </div>

    <p v-if="turn.error" class="alert-red break-words">{{ turn.error }}</p>

    <div class="grid gap-3 md:grid-cols-2">
      <div>
        <p class="label">{{ t('trace.input') }}</p>
        <p class="pre">{{ turn.input_text || '—' }}</p>
      </div>
      <div>
        <p class="label">{{ t('trace.rewritten') }}</p>
        <p class="pre">{{ turn.rewritten || '—' }}</p>
      </div>
    </div>

    <!-- route answers -->
    <div v-if="answers.length">
      <p class="label">{{ t('trace.route') }}</p>
      <div class="flex flex-wrap gap-1.5">
        <span v-for="a in answers" :key="a.key" :class="a.on ? 'badge-blue' : 'badge-gray'" class="code">
          {{ a.key }} = {{ a.value }}
        </span>
      </div>
    </div>

    <!-- why scenarios started or not -->
    <div v-if="startSums.length">
      <p class="label">{{ t('trace.start_votes') }}</p>
      <table class="table">
        <thead>
          <tr>
            <th>{{ t('trace.scenario') }}</th>
            <th>{{ t('trace.sum') }}</th>
            <th>{{ t('trace.threshold') }}</th>
            <th>{{ t('trace.yes_votes') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in startSums" :key="s.key">
            <td class="code">
              {{ s.key }}
              <span v-if="started.includes(s.key)" class="badge-green ml-1">{{ t('trace.started') }}</span>
              <span v-else-if="s.fired" class="badge-blue ml-1">{{ t('trace.fired') }}</span>
            </td>
            <td>{{ s.sum ?? '—' }}</td>
            <td>{{ s.threshold ?? '—' }}</td>
            <td class="code text-xs">{{ s.yes || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- steps -->
    <div v-if="steps.length">
      <p class="label">{{ t('trace.steps') }}</p>
      <div class="space-y-1.5">
        <details v-for="(s, i) in steps" :key="i" class="rounded-button border border-slate-200 dark:border-slate-700">
          <summary class="flex items-center gap-2 px-3 py-2 cursor-pointer">
            <span class="code font-medium">{{ s.step }}</span>
            <span class="badge-gray">{{ f.ms(s.ms) }}</span>
            <span v-if="s.model" class="badge-violet code">{{ s.model }}</span>
            <span v-if="s.error" class="badge-red">{{ t('trace.error') }}</span>
          </summary>
          <div class="px-3 pb-3"><JsonView :value="s" /></div>
        </details>
      </div>
    </div>

    <!-- decisions -->
    <div v-if="decisions.length">
      <p class="label">{{ t('trace.decisions') }}</p>
      <div class="overflow-x-auto">
        <table class="table">
          <thead>
            <tr>
              <th>{{ t('trace.question') }}</th>
              <th>{{ t('trace.answer') }}</th>
              <th>{{ t('trace.confidence') }}</th>
              <th>{{ t('trace.provider') }}</th>
              <th>{{ t('trace.cost') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="d in decisions" :key="d.id" :class="d.used ? '' : 'opacity-60'">
              <td>
                <span class="code">{{ d.key }}</span>
                <p v-if="d.question" class="text-xs muted mt-0.5 line-clamp-2" :title="d.question">{{ d.question }}</p>
              </td>
              <td class="code">{{ answerText(d) }}</td>
              <td>
                <span :class="confidenceClass(d.confidence)">{{ d.confidence != null ? f.num(d.confidence * 100, 0) + '%' : '—' }}</span>
              </td>
              <td>
                <span class="badge-gray">{{ d.provider }}</span>
                <span v-if="d.reason && d.reason !== 'primary'" class="badge-amber ml-1">{{ t(`trace.reason.${d.reason}`) }}</span>
                <span v-if="!d.used" class="badge-gray ml-1">{{ t('trace.not_used') }}</span>
                <p v-if="d.error" class="text-xs text-rose-600 mt-0.5 break-words">{{ d.error }}</p>
              </td>
              <td class="whitespace-nowrap">{{ f.money(d.cost) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- LLM calls -->
    <div v-if="calls.length">
      <p class="label">{{ t('trace.llm_calls') }}</p>
      <div class="space-y-1.5">
        <details v-for="c in calls" :key="c.id" class="rounded-button border border-slate-200 dark:border-slate-700">
          <summary class="flex flex-wrap items-center gap-2 px-3 py-2 cursor-pointer">
            <span class="badge-blue">{{ c.role }}</span>
            <span class="code">{{ c.model }}</span>
            <span v-if="c.prompt_key" class="badge-gray code">{{ c.prompt_key }} v{{ c.prompt_version }}</span>
            <span class="badge-gray">{{ f.ms(c.latency_ms) }}</span>
            <span class="badge-gray">{{ f.money(c.cost) }}</span>
            <span v-if="c.attempt > 1" class="badge-amber">{{ t('trace.attempt', { n: c.attempt }) }}</span>
            <span v-if="c.error" class="badge-red">{{ t('trace.error') }}</span>
          </summary>
          <div class="px-3 pb-3 space-y-2">
            <p class="text-xs muted">
              {{ t('trace.tokens_full', { in: c.tokens_in ?? 0, cached: c.tokens_cached ?? 0, out: c.tokens_out ?? 0 }) }}
              <span v-if="c.generation_id" class="code ml-2">{{ c.generation_id }}</span>
            </p>
            <p v-if="c.error" class="alert-red text-xs break-words">{{ c.error }}</p>
            <JsonView :value="c.request" collapsible :label="t('trace.request')" />
            <JsonView :value="c.response" collapsible :label="t('trace.response')" />
          </div>
        </details>
      </div>
    </div>

    <JsonView :value="detail.turn.route" collapsible :label="t('trace.raw_route')" />
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{ detail: any }>()
const { t } = useI18n()
const f = useFormat()

const turn = computed(() => props.detail.turn)
const steps = computed(() => (props.detail.turn.trace?.steps || []) as any[])
const decisions = computed(() => (props.detail.decisions || []) as any[])
const calls = computed(() => (props.detail.llm_calls || []) as any[])

const answers = computed(() => {
  const raw = props.detail.turn.route?.answers || {}
  return Object.entries(raw).map(([key, value]) => ({
    key,
    value: typeof value === 'object' ? JSON.stringify(value) : String(value),
    on: value === true || (typeof value === 'string' && value !== 'none'),
  }))
})

const started = computed(() => (props.detail.turn.route?.scenarios?.started || []) as string[])
const startSums = computed(() => {
  const sums = props.detail.turn.route?.scenarios?.start || {}
  return Object.entries(sums).map(([key, v]: [string, any]) => ({
    key,
    sum: v?.sum,
    threshold: v?.threshold,
    fired: !!v?.fired,
    yes: Array.isArray(v?.yes) ? v.yes.join(', ') : '',
  }))
})

function answerText(d: any) {
  const a = d.answer || {}
  if ('value' in a) return typeof a.value === 'object' ? JSON.stringify(a.value) : String(a.value)
  return JSON.stringify(a)
}

function confidenceClass(c: number | null) {
  if (c == null) return 'badge-gray'
  if (c >= 0.85) return 'badge-green'
  if (c >= 0.65) return 'badge-amber'
  return 'badge-red'
}
</script>
