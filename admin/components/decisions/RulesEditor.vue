<template>
  <div class="space-y-2">
    <p v-if="!rows.length" class="text-sm muted">{{ t('decisions.no_rules') }}</p>
    <div v-if="rows.length" class="hidden md:grid gap-2 text-xs muted md:grid-cols-[minmax(10rem,1.4fr)_10rem_6rem_12rem_minmax(8rem,1fr)_auto]">
      <span>{{ t('decisions.pattern') }}</span>
      <span>{{ t('decisions.mode_title') }}</span>
      <span>{{ t('decisions.threshold') }}</span>
      <span>{{ t('decisions.below_title') }}</span>
      <span>{{ t('decisions.note') }}</span>
      <span class="w-9" />
    </div>
    <div v-for="(r, i) in rows" :key="i" class="grid gap-2 items-start md:grid-cols-[minmax(10rem,1.4fr)_10rem_6rem_12rem_minmax(8rem,1fr)_auto]">
      <div>
        <label class="label md:sr-only">{{ t('decisions.pattern') }}</label>
        <input v-model.trim="r.pattern" class="input code text-xs" required maxlength="200" placeholder="start:*" />
      </div>
      <div>
        <label class="label md:sr-only">{{ t('decisions.mode_title') }}</label>
        <select v-model="r.mode" class="select">
          <option v-for="m in MODES" :key="m" :value="m">{{ t(`decisions.mode.${m}`) }}</option>
        </select>
      </div>
      <div>
        <label class="label md:sr-only">{{ t('decisions.threshold') }}</label>
        <input v-model.number="r.threshold" type="number" min="0" max="1" step="0.01" class="input" :disabled="r.mode === 'llm'" />
      </div>
      <div>
        <label class="label md:sr-only">{{ t('decisions.below_title') }}</label>
        <select v-model="r.below" class="select" :disabled="r.mode === 'llm'">
          <option v-for="b in BELOW" :key="b" :value="b">{{ t(`decisions.below.${b}`) }}</option>
        </select>
      </div>
      <div>
        <label class="label md:sr-only">{{ t('decisions.note') }}</label>
        <input v-model="r.note" class="input" maxlength="500" :placeholder="t('decisions.note')" />
      </div>
      <button type="button" class="btn-icon btn-ghost text-rose-600 md:mt-0.5" :aria-label="t('common.delete')" @click="rows.splice(i, 1)">
        <UiIcon name="trash" :size="16" />
      </button>
    </div>
    <button type="button" class="btn-ghost btn-sm" @click="add">
      <UiIcon name="plus" :size="14" /> {{ t('decisions.add_rule') }}
    </button>
  </div>
</template>

<script setup lang="ts">
interface RuleRow {
  pattern: string
  mode: string
  threshold: number
  below: string
  note: string
}

const rows = defineModel<RuleRow[]>({ required: true })
const { t } = useI18n()

const MODES = ['jev', 'llm', 'shadow']
const BELOW = ['safe', 'llm', 'keep', 'ask_client', 'operator']

function add() {
  rows.value.push({ pattern: '', mode: 'jev', threshold: 0.7, below: 'safe', note: '' })
}
</script>
