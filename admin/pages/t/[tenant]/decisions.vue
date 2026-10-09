<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="page-title">{{ t('nav.decisions') }}</h1>
        <p class="page-sub">{{ t('decisions.sub') }}</p>
      </div>
      <div class="flex flex-wrap items-end gap-3">
        <div class="w-32">
          <label class="label">{{ t('decisions.period') }}</label>
          <select v-model.number="days" class="select" @change="load">
            <option v-for="d in [1, 7, 30, 90]" :key="d" :value="d">{{ t('decisions.days', { n: d }) }}</option>
          </select>
        </div>
        <button type="button" class="btn-primary" @click="openRules(null)">
          <UiIcon name="settings" :size="16" /> {{ t('decisions.rules') }}
        </button>
      </div>
    </div>

    <UiState :loading="loading" :error="error" :has-data="!!data" :retry="load">
      <template v-if="data">
        <div class="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
          <div class="card card-body">
            <p class="text-sm muted">{{ t('decisions.total.asked') }}</p>
            <p class="text-2xl font-bold mt-1">{{ f.num(data.totals.asked) }}</p>
          </div>
          <div class="card card-body">
            <p class="text-sm muted">{{ t('decisions.total.jev_alone') }}</p>
            <p class="text-2xl font-bold mt-1">{{ pct(data.totals.jev_used, data.totals.asked) }}</p>
            <p class="hint">{{ t('decisions.of', { n: f.num(data.totals.jev_used), total: f.num(data.totals.asked) }) }}</p>
          </div>
          <div class="card card-body">
            <p class="text-sm muted">{{ t('decisions.total.to_llm') }}</p>
            <p class="text-2xl font-bold mt-1">{{ f.num(data.totals.below_threshold + data.totals.jev_error) }}</p>
            <p class="hint">{{ t('decisions.to_llm_split', { below: f.num(data.totals.below_threshold), errors: f.num(data.totals.jev_error) }) }}</p>
          </div>
          <div class="card card-body">
            <p class="text-sm muted">{{ t('decisions.total.unsure') }}</p>
            <p class="text-2xl font-bold mt-1">{{ f.num(data.totals.unsure) }}</p>
          </div>
          <div class="card card-body">
            <p class="text-sm muted">{{ t('decisions.total.agree') }}</p>
            <p class="text-2xl font-bold mt-1">{{ data.totals.pairs ? pct(data.totals.agree, data.totals.pairs) : '—' }}</p>
            <p class="hint">{{ t('decisions.compared', { n: f.num(data.totals.pairs) }) }}</p>
          </div>
        </div>

        <div class="card overflow-hidden">
          <UiState :loading="false" :error="null" :empty="!data.items.length ? t('decisions.empty') : false" :has-data="data.items.length > 0">
            <div class="overflow-x-auto">
              <table class="table">
                <thead>
                  <tr>
                    <th>{{ t('decisions.col.question') }}</th>
                    <th class="text-right">{{ t('decisions.col.asked') }}</th>
                    <th class="text-right">{{ t('decisions.col.jev_alone') }}</th>
                    <th class="text-right">{{ t('decisions.col.confidence') }}</th>
                    <th class="text-right">{{ t('decisions.col.to_llm') }}</th>
                    <th class="text-right">{{ t('decisions.col.unsure') }}</th>
                    <th class="text-right">{{ t('decisions.col.agree') }}</th>
                    <th>{{ t('decisions.col.rule') }}</th>
                  </tr>
                </thead>
                <tbody>
                  <template v-for="g in data.items" :key="g.group">
                    <tr class="cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800/40" @click="toggle(g)">
                      <td class="code text-xs break-all">{{ g.group }}</td>
                      <td class="text-right">{{ f.num(g.asked) }}</td>
                      <td class="text-right">{{ pct(g.jev_used, g.asked) }}</td>
                      <td class="text-right">{{ g.jev_confidence !== null ? f.num(g.jev_confidence, 2) : '—' }}</td>
                      <td class="text-right">
                        {{ f.num(g.below_threshold + g.jev_error) }}
                        <span v-if="g.jev_error" class="badge-red ml-1" :title="t('decisions.col.errors')">{{ g.jev_error }}</span>
                      </td>
                      <td class="text-right">{{ f.num(g.unsure) }}</td>
                      <td class="text-right">
                        <span v-if="g.pairs" :class="agreeClass(g.agree / g.pairs)">{{ pct(g.agree, g.pairs) }}</span>
                        <span v-else class="muted">—</span>
                      </td>
                      <td class="text-xs whitespace-nowrap">
                        <span class="font-medium">{{ t(`decisions.mode.${g.rule.mode}`) }}</span>
                        <span class="muted"> · {{ f.num(g.rule.threshold, 2) }} · {{ t(`decisions.below.${g.rule.below}`) }}</span>
                        <span class="badge-gray ml-1">{{ t(`decisions.source.${g.rule_source || 'default'}`) }}</span>
                      </td>
                    </tr>
                    <tr v-if="open === g.group">
                      <td colspan="8" class="bg-slate-50/60 dark:bg-slate-900/40">
                        <div class="grid gap-5 lg:grid-cols-2 py-2">
                          <div>
                            <h3 class="font-semibold text-sm mb-1">{{ t('decisions.spread') }}</h3>
                            <p class="hint mb-3">{{ t('decisions.spread_hint') }}</p>
                            <div class="flex items-end gap-0.5 h-32">
                              <div
                                v-for="b in g.buckets"
                                :key="b.from"
                                class="relative flex-1 rounded-t bg-slate-200 dark:bg-slate-700"
                                :class="{ 'ring-2 ring-primary/60': Math.abs(b.from - sim) < 0.001 }"
                                :style="{ height: `${barHeight(g, b.jev)}%` }"
                                :title="barTitle(b)"
                              >
                                <div
                                  v-if="b.pairs"
                                  class="absolute bottom-0 inset-x-0 rounded-t bg-emerald-500/80"
                                  :style="{ height: `${(b.agree / b.pairs) * 100}%` }"
                                />
                              </div>
                            </div>
                            <div class="flex justify-between text-xs muted mt-1"><span>0</span><span>0.5</span><span>1</span></div>
                          </div>

                          <div class="space-y-3">
                            <h3 class="font-semibold text-sm">{{ t('decisions.simulate') }}</h3>
                            <div class="flex items-center gap-3">
                              <input v-model.number="sim" type="range" min="0.5" max="1" step="0.05" class="flex-1" />
                              <span class="code text-sm w-12 text-right">{{ sim.toFixed(2) }}</span>
                            </div>
                            <ul class="text-sm space-y-1">
                              <li>{{ t('decisions.sim_alone', { pct: pct(simulate(g).above, simulate(g).total) }) }}</li>
                              <li>{{ t('decisions.sim_llm', { pct: pct(simulate(g).total - simulate(g).above, simulate(g).total) }) }}</li>
                              <template v-if="g.pairs">
                                <li>{{ t('decisions.sim_agree_above', { pct: pct(simulate(g).agreeAbove, simulate(g).pairsAbove), n: simulate(g).pairsAbove }) }}</li>
                                <li>{{ t('decisions.sim_agree_below', { pct: pct(simulate(g).agreeBelow, simulate(g).pairsBelow), n: simulate(g).pairsBelow }) }}</li>
                              </template>
                              <li v-else class="muted">{{ t('decisions.sim_no_pairs') }}</li>
                            </ul>
                            <div class="flex flex-wrap gap-2">
                              <button type="button" class="btn-secondary btn-sm" @click.stop="openRules(g)">
                                {{ t('decisions.use_threshold') }}
                              </button>
                              <button v-if="g.pairs" type="button" class="btn-ghost btn-sm" @click.stop="loadDisagreements(g)">
                                {{ t('decisions.show_disagreements') }}
                              </button>
                            </div>
                            <p class="text-xs muted">
                              {{ t('decisions.last', { at: f.relative(g.last_at) }) }} ·
                              {{ t('decisions.jev_latency', { ms: f.ms(g.jev_latency_ms) }) }} ·
                              {{ t('decisions.cost_split', { jev: f.money(g.jev_cost), llm: f.money(g.llm_cost) }) }}
                            </p>
                          </div>
                        </div>

                        <div v-if="diff.group === g.group" class="mt-3">
                          <h3 class="font-semibold text-sm mb-2">{{ t('decisions.disagreements') }}</h3>
                          <UiState :loading="diff.loading" :error="diff.error" :empty="!diff.loading && !diff.items.length ? t('decisions.no_disagreements') : false" :has-data="diff.items.length > 0">
                            <div class="space-y-2">
                              <div v-for="d in diff.items" :key="`${d.turn_id}:${d.key}`" class="card card-body text-sm space-y-1">
                                <div class="flex flex-wrap items-center justify-between gap-2">
                                  <span class="code text-xs">{{ d.key }} · {{ f.dateTime(d.created_at) }}</span>
                                  <NuxtLink v-if="d.conversation_id" :to="`${base}/inbox/${d.conversation_id}`" class="text-xs font-medium text-primary hover:underline">
                                    {{ t('decisions.open') }}
                                  </NuxtLink>
                                </div>
                                <p><span class="muted">{{ t('decisions.client_text') }}:</span> {{ d.rewritten || d.input_text }}</p>
                                <p class="text-xs muted">{{ d.instructions }}</p>
                                <div class="flex flex-wrap gap-4 text-xs">
                                  <span>
                                    {{ t('decisions.jev') }}: <b class="code">{{ show(d.jev.value) }}</b>
                                    ({{ f.num(d.jev.confidence, 2) }}{{ d.jev.used ? ', ' + t('decisions.used') : '' }})
                                  </span>
                                  <span>
                                    {{ t('decisions.llm') }}: <b class="code">{{ show(d.llm.value) }}</b>
                                    ({{ f.num(d.llm.confidence, 2) }}{{ d.llm.used ? ', ' + t('decisions.used') : '' }})
                                  </span>
                                </div>
                              </div>
                            </div>
                          </UiState>
                        </div>
                      </td>
                    </tr>
                  </template>
                </tbody>
              </table>
            </div>
          </UiState>
          <p class="hint px-5 pb-4">{{ t('decisions.keys_hint') }}</p>
        </div>
      </template>
    </UiState>

    <UiModal :open="rulesOpen" :title="t('decisions.rules_title')" size="xl" @close="rulesOpen = false">
      <div class="space-y-5">
        <p class="text-sm muted">{{ t('decisions.rules_sub', { threshold: policy?.default_rule?.threshold ?? 0.7 }) }}</p>
        <ul class="text-xs muted space-y-1">
          <li v-for="m in ['jev', 'llm', 'shadow']" :key="m"><b>{{ t(`decisions.mode.${m}`) }}</b> — {{ t(`decisions.mode_hint.${m}`) }}</li>
          <li v-for="b in BELOW" :key="b"><b>{{ t(`decisions.below.${b}`) }}</b> — {{ t(`decisions.below_hint.${b}`) }}</li>
        </ul>

        <section>
          <h3 class="font-semibold text-sm mb-2">{{ editShared ? t('decisions.shared_rules') : t('decisions.own_rules') }}</h3>
          <RulesEditor v-model="rules" />
        </section>

        <section v-if="!editShared && policy">
          <div class="flex flex-wrap items-center justify-between gap-2 mb-2">
            <h3 class="font-semibold text-sm">{{ t('decisions.shared_rules') }}</h3>
            <button v-if="policy.can_edit_shared" type="button" class="btn-ghost btn-sm" @click="startSharedEdit">
              <UiIcon name="edit" :size="14" /> {{ t('decisions.edit_shared') }}
            </button>
          </div>
          <p class="hint mb-2">{{ t('decisions.shared_hint') }}</p>
          <p v-if="!policy.shared.length" class="text-sm muted">{{ t('decisions.no_rules') }}</p>
          <ul v-else class="text-sm space-y-1">
            <li v-for="r in policy.shared" :key="r.id" class="code text-xs">
              {{ r.pattern }} — {{ t(`decisions.mode.${r.mode}`) }} · {{ f.num(r.threshold, 2) }} · {{ t(`decisions.below.${r.below}`) }}
              <span v-if="r.note" class="muted"> ({{ r.note }})</span>
            </li>
          </ul>
        </section>
        <p v-if="rulesError" class="text-sm text-rose-600">{{ rulesError }}</p>
      </div>
      <template #footer>
        <button type="button" class="btn-secondary" @click="rulesOpen = false">{{ t('common.cancel') }}</button>
        <button type="button" class="btn-primary" :disabled="busy" @click="saveRules">{{ t('common.save') }}</button>
      </template>
    </UiModal>
  </div>
</template>

<script setup lang="ts">
const { t } = useI18n()
const api = useApi()
const f = useFormat()
const toast = useToast()
const { errorText } = useErrorText()
const { path, base } = useTenant()
usePage(() => [{ label: t('nav.decisions') }])

const BELOW = ['llm', 'keep', 'ask_client', 'operator']

const days = ref(7)
const open = ref<string | null>(null)
const sim = ref(0.7)

const { data, loading, error, load } = useLoader(() => api<any>(path('/decisions/stats'), { query: { days: days.value } }))

function pct(n: number, total: number) {
  if (!total) return '—'
  return `${f.num((n / total) * 100, n === total ? 0 : 1)}%`
}
function agreeClass(share: number) {
  if (share >= 0.95) return 'text-emerald-600 font-medium'
  if (share >= 0.85) return 'text-amber-600 font-medium'
  return 'text-rose-600 font-medium'
}
function show(v: unknown) {
  return typeof v === 'string' ? v : JSON.stringify(v)
}

function toggle(g: any) {
  open.value = open.value === g.group ? null : g.group
  sim.value = Math.min(1, Math.max(0.5, Math.round(g.rule.threshold * 20) / 20))
}
function barHeight(g: any, n: number) {
  const max = Math.max(...g.buckets.map((b: any) => b.jev), 1)
  return n ? Math.max(3, (n / max) * 100) : 0
}
function barTitle(b: any) {
  const to = Math.min(1, b.from + (data.value?.bucket_size || 0.05))
  return t('decisions.bar', { from: b.from.toFixed(2), to: to.toFixed(2), n: b.jev, agree: b.agree, pairs: b.pairs })
}
// what a threshold would change, by the buckets (a bucket counts as above when it starts at it)
function simulate(g: any) {
  const out = { total: 0, above: 0, pairsAbove: 0, agreeAbove: 0, pairsBelow: 0, agreeBelow: 0 }
  for (const b of g.buckets) {
    out.total += b.jev
    if (b.from + 1e-9 >= sim.value) {
      out.above += b.jev
      out.pairsAbove += b.pairs
      out.agreeAbove += b.agree
    } else {
      out.pairsBelow += b.pairs
      out.agreeBelow += b.agree
    }
  }
  return out
}

// --- disagreements ---
const diff = reactive({ group: '', loading: false, error: null as any, items: [] as any[] })
async function loadDisagreements(g: any) {
  Object.assign(diff, { group: g.group, loading: true, error: null, items: [] })
  try {
    diff.items = (await api<{ items: any[] }>(path('/decisions/disagreements'), { query: { key: g.group, days: days.value } })).items
  } catch (e) {
    diff.error = e
  } finally {
    diff.loading = false
  }
}

// --- rules ---
interface RuleRow {
  pattern: string
  mode: string
  threshold: number
  below: string
  note: string
}
const policy = ref<any>(null)
const rules = ref<RuleRow[]>([])
const rulesOpen = ref(false)
const editShared = ref(false)
const busy = ref(false)
const rulesError = ref<string | null>(null)

const toRow = (r: any): RuleRow => ({ pattern: r.pattern, mode: r.mode, threshold: r.threshold, below: r.below, note: r.note || '' })

async function openRules(g: any | null) {
  rulesError.value = null
  editShared.value = false
  try {
    policy.value = await api<any>(path('/decisions/policy'))
  } catch (e) {
    toast.error(errorText(e))
    return
  }
  rules.value = policy.value.own.map(toRow)
  if (g) {
    // the threshold from the simulator for this question group
    const own = rules.value.find((r) => r.pattern === g.group)
    if (own) own.threshold = sim.value
    else rules.value.push({ ...toRow(g.rule), pattern: g.group, threshold: sim.value, note: '' })
  }
  rulesOpen.value = true
}

function startSharedEdit() {
  editShared.value = true
  rules.value = policy.value.shared.map(toRow)
}

async function saveRules() {
  busy.value = true
  rulesError.value = null
  const body = { rules: rules.value.map((r) => ({ ...r, note: r.note || null })) }
  try {
    if (editShared.value) await api('/decisions/policy', { method: 'PUT', body })
    else await api(path('/decisions/policy'), { method: 'PUT', body })
    rulesOpen.value = false
    toast.ok(t('decisions.saved'))
    load()
  } catch (e) {
    rulesError.value = errorText(e)
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>
