<template>
  <div class="space-y-2.5">
    <!-- step 2: the questions in the order the bot asks them; one is open at a time -->
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

const props = defineProps<{ def: any; languages: string[]; readonly?: boolean; categories?: string[] }>()
const { t } = useI18n()
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
