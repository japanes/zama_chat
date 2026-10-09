<template>
  <div class="space-y-3">
    <!-- step 3: the summary the client confirms before the request goes anywhere -->
    <label class="flex items-center gap-2.5 min-h-[44px]">
      <input type="checkbox" class="checkbox" :checked="on" :disabled="readonly" @change="toggle" />
      <span class="font-medium text-slate-900 dark:text-white">{{ t('editor.confirm.on') }}</span>
    </label>
    <div v-if="on">
      <label class="label" :for="`${uid}-ask`">{{ t('editor.confirm.ask') }}</label>
      <LocalizedInput
        :model-value="def.confirm?.question"
        :input-id="`${uid}-ask`"
        :languages="languages"
        :main="def.language"
        optional
        :readonly="readonly"
        :placeholder="t('scenarios.confirm_default')"
        @update:model-value="setQuestion"
      />
      <p class="hint">{{ t('editor.confirm.hint') }}</p>
    </div>
    <p v-else class="text-sm muted">{{ t('editor.confirm.off_hint') }}</p>
  </div>
</template>

<script setup lang="ts">
import type { LText } from '~/utils/scenario'

const props = defineProps<{ def: any; languages: string[]; readonly?: boolean }>()
const { t } = useI18n()
const uid = useId()

// «the client's yes is required» is the same thing written in two ways in the format
const on = computed(() => !!props.def.confirm || props.def.completion === 'explicit_yes')

function toggle() {
  if (on.value) {
    props.def.confirm = null
    if (props.def.completion === 'explicit_yes') props.def.completion = 'all_fields'
  } else {
    props.def.confirm = { question: null }
  }
}
function setQuestion(value: LText | null) {
  props.def.confirm = { ...(props.def.confirm || {}), question: value }
}
</script>
