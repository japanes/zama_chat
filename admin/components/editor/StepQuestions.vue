<template>
  <div class="space-y-2.5">
    <!-- only_scenario: while a run of it is open, the turn is the scenario's alone -->
    <div class="rounded-button bg-slate-50 dark:bg-slate-900/40 px-4 py-3.5">
      <label class="flex items-center gap-2.5 min-h-[44px]">
        <input v-model="def.only_scenario" type="checkbox" class="checkbox" :disabled="readonly" />
        <span class="text-[15px] font-medium text-slate-900 dark:text-slate-100">{{ t('editor.q.only_scenario') }}</span>
      </label>
      <p class="hint">{{ t('editor.q.only_scenario_hint') }}</p>
    </div>

    <!-- what the bot reads when it words a question: the answers always, the last messages,
         and the scenario's own rules for wording (only the bot that writes sees them) -->
    <div class="rounded-button bg-slate-50 dark:bg-slate-900/40 px-4 py-3.5 space-y-3">
      <p class="text-[13px] font-semibold text-slate-600 dark:text-slate-300">{{ t('editor.q.memory') }}</p>
      <p class="text-sm text-slate-600 dark:text-slate-300">{{ t('editor.q.memory_hint') }}</p>
      <div>
        <label class="label" :for="`${uid}-history`">{{ t('editor.q.history') }}</label>
        <input
          :id="`${uid}-history`"
          v-model="def.history_messages"
          type="number"
          min="1"
          max="200"
          class="input sm:!w-40"
          :placeholder="historyDefault ? String(historyDefault) : ''"
          :readonly="readonly"
        />
        <p class="hint">{{ historyDefault ? t('editor.q.history_default', { n: historyDefault }) : t('editor.q.history_default_unknown') }}</p>
      </div>
      <div>
        <p class="label">{{ t('editor.q.ask_rules') }}</p>
        <div class="space-y-2">
          <div v-for="(rule, i) in (def.ask_rules as any[])" :key="i" class="flex items-start gap-2">
            <LocalizedInput
              :model-value="rule"
              class="flex-1 min-w-0"
              :languages="languages"
              :main="def.language"
              multiline
              :rows="2"
              :readonly="readonly"
              :aria-label="t('editor.q.rule_n', { n: i + 1 })"
              :placeholder="t('editor.q.rule_placeholder')"
              @update:model-value="def.ask_rules[i] = $event ?? {}"
            />
            <button v-if="!readonly" type="button" class="btn-icon btn-ghost" :aria-label="t('editor.q.rule_remove')" :title="t('editor.q.rule_remove')" @click="def.ask_rules.splice(i, 1)">
              <UiIcon name="close" :size="18" />
            </button>
          </div>
        </div>
        <p class="hint">{{ t('editor.q.ask_rules_hint') }}</p>
        <button v-if="!readonly" type="button" class="btn-dashed btn-sm mt-2" @click="def.ask_rules.push({})">
          <UiIcon name="plus" :size="14" /> {{ t('editor.q.add_rule') }}
        </button>
      </div>
    </div>

    <QuestionCard
      v-for="(f, i) in (def.fields as any[])"
      :key="i"
      :def="def"
      :field="f"
      :index="i"
      :last="i === def.fields.length - 1"
      :open="opened === i"
      :languages="languages"
      :readonly="readonly"
      :categories="categories"
      @toggle="opened = opened === i ? null : i"
      @move="move(i, $event)"
      @remove="remove(i)"
    />
    <button v-if="!readonly" type="button" class="btn-dashed" @click="add">
      <UiIcon name="plus" :size="16" /> {{ t('editor.q.add') }}
    </button>
  </div>
</template>

<script setup lang="ts">
import { fieldName, newField, slugKey } from '~/utils/scenario'

const props = defineProps<{
  def: any
  languages: string[]
  readonly?: boolean
  categories?: string[]
  // how many last messages a scenario without its own number reads (the server's setting)
  historyDefault?: number
}>()
const { t } = useI18n()
const uid = useId()
const confirm = useConfirm()
const ctx = useEditorContext()

// the index of the open question
const opened = ref<number | null>(null)

function add() {
  const list = props.def.fields as any[]
  const field = newField(list.length + 1)
  field.key = slugKey('', list.map((f) => f.key), 'question')
  list.push(field)
  opened.value = list.length - 1
}

function move(i: number, dir: number) {
  const list = props.def.fields as any[]
  const j = i + dir
  if (j < 0 || j >= list.length) return
  ;[list[i], list[j]] = [list[j], list[i]]
  if (opened.value === i) opened.value = j
  else if (opened.value === j) opened.value = i
}

async function remove(i: number) {
  const list = props.def.fields as any[]
  const name = fieldName(list[i], ctx?.lang.value, props.def.language) || t('editor.q.untitled')
  const ok = await confirm.confirm({
    message: t('editor.q.remove_confirm', { name }),
    tone: 'danger',
    confirmLabel: t('editor.q.remove'),
  })
  if (!ok) return
  const [gone] = list.splice(i, 1)
  opened.value = null
  // what pointed at the removed question goes with it
  const d = props.def
  d.start.questions = (d.start.questions as any[]).filter((q) => q.field !== gone.key)
  for (const f of list) {
    f.ask_after = (f.ask_after || []).filter((k: string) => k !== gone.key)
    if (f.receipt?.amount_field === gone.key) f.receipt.amount_field = ''
    if (f.receipt?.quantity_field === gone.key) f.receipt.quantity_field = ''
  }
  for (const e of d.events_list || []) e.reopen_fields = (e.reopen_fields || []).filter((k: string) => k !== gone.key)
  for (const a of d.actions || []) if (a.field === gone.key) a.field = undefined
}
</script>
