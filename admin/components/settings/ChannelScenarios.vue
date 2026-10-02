<template>
  <!-- the scenarios of one channel: which are on and in what order (the order decides who
       gets the focus when two scenarios score the same) -->
  <UiState :loading="loading" :error="error" :has-data="loaded" :retry="load">
    <div class="space-y-4">
      <p class="hint">{{ t('channels.order_hint') }}</p>
      <ul class="space-y-1.5">
        <li
          v-for="(s, i) in items"
          :key="s.key"
          class="flex items-center gap-2 rounded-button border px-3 py-2 bg-white dark:bg-slate-800"
          :class="[dragIndex === i ? 'opacity-50' : '', overIndex === i ? 'border-primary' : 'border-slate-200 dark:border-slate-700']"
          :draggable="canEdit"
          @dragstart="dragIndex = i"
          @dragover.prevent="overIndex = i"
          @dragleave="overIndex = null"
          @drop.prevent="drop(i)"
          @dragend="dragIndex = overIndex = null"
        >
          <UiIcon v-if="canEdit" name="drag" :size="16" class="text-slate-400 cursor-grab" />
          <span class="w-6 text-xs muted">{{ i + 1 }}</span>
          <div class="flex-1 min-w-0">
            <span class="font-medium">{{ s.title || s.key }}</span>
            <span class="code text-xs muted ml-2">{{ s.key }}</span>
            <span v-if="!s.active_version" class="badge-amber ml-2">{{ t('channels.scenario_off') }}</span>
          </div>
          <label class="flex items-center gap-1.5 text-sm">
            <input v-model="s.is_enabled" type="checkbox" class="checkbox" :disabled="!canEdit" @change="dirty = true" />
            <span class="hidden sm:inline">{{ t('channels.enabled') }}</span>
          </label>
          <template v-if="canEdit">
            <button type="button" class="btn-icon btn-ghost btn-sm" :disabled="i === 0" :aria-label="t('common.move_up')" @click="move(i, -1)">
              <UiIcon name="chevron-up" :size="14" />
            </button>
            <button type="button" class="btn-icon btn-ghost btn-sm" :disabled="i === items.length - 1" :aria-label="t('common.move_down')" @click="move(i, 1)">
              <UiIcon name="chevron-down" :size="14" />
            </button>
            <button type="button" class="btn-icon btn-ghost btn-sm text-rose-600" :aria-label="t('common.delete')" @click="remove(i)">
              <UiIcon name="close" :size="14" />
            </button>
          </template>
        </li>
      </ul>
      <p v-if="!items.length" class="text-sm muted">{{ t('channels.no_scenarios') }}</p>

      <div v-if="canEdit && available.length" class="flex flex-wrap items-center gap-2">
        <select v-model="toAdd" class="select w-64">
          <option value="">{{ t('channels.add_scenario') }}</option>
          <option v-for="a in available" :key="a.key" :value="a.key">{{ a.title || a.key }}</option>
        </select>
        <button type="button" class="btn-secondary btn-sm" :disabled="!toAdd" @click="add">
          <UiIcon name="plus" :size="14" /> {{ t('common.add') }}
        </button>
      </div>

      <div v-if="canEdit" class="flex justify-end">
        <button type="button" class="btn-primary" :disabled="!dirty || busy" @click="save">{{ t('common.save') }}</button>
      </div>
    </div>
  </UiState>
</template>

<script setup lang="ts">
const props = defineProps<{ channelId: string; canEdit: boolean }>()
const { t } = useI18n()
const api = useApi()
const { path } = useTenant()
const { busy, run } = useAction()

const items = ref<any[]>([])
const available = ref<any[]>([])
const loaded = ref(false)
const dirty = ref(false)
const toAdd = ref('')
const dragIndex = ref<number | null>(null)
const overIndex = ref<number | null>(null)

const { loading, error, load } = useLoader(async () => {
  const res = await api<any>(path(`/channels/${props.channelId}/scenarios`))
  items.value = res.items
  available.value = res.available
  loaded.value = true
  dirty.value = false
  return res
})

function move(i: number, dir: number) {
  const j = i + dir
  ;[items.value[i], items.value[j]] = [items.value[j], items.value[i]]
  dirty.value = true
}
function drop(i: number) {
  const from = dragIndex.value
  dragIndex.value = overIndex.value = null
  if (from === null || from === i) return
  const [row] = items.value.splice(from, 1)
  items.value.splice(i, 0, row)
  dirty.value = true
}
function remove(i: number) {
  const [row] = items.value.splice(i, 1)
  available.value.push({ key: row.key, title: row.title, active_version: row.active_version })
  dirty.value = true
}
function add() {
  const i = available.value.findIndex((a) => a.key === toAdd.value)
  if (i < 0) return
  const [row] = available.value.splice(i, 1)
  items.value.push({ ...row, is_enabled: true })
  toAdd.value = ''
  dirty.value = true
}
async function save() {
  const res = await run(
    () =>
      api(path(`/channels/${props.channelId}/scenarios`), {
        method: 'PUT',
        body: { items: items.value.map((s) => ({ key: s.key, is_enabled: s.is_enabled })) },
      }),
    t('common.saved'),
  )
  if (res) load()
}

watch(() => props.channelId, load)
onMounted(load)
</script>
