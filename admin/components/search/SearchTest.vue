<template>
  <div class="card card-body space-y-3">
    <h2 class="card-title">{{ t('search.test_title') }}</h2>
    <p class="hint -mt-1">{{ t('search.test_hint') }}</p>
    <form class="flex flex-wrap items-end gap-3" @submit.prevent="search">
      <div class="flex-1 min-w-[14rem]">
        <label class="label">{{ t('search.query') }}</label>
        <input v-model="query" class="input" required maxlength="2000" />
      </div>
      <div>
        <label class="label">{{ t('search.kind_label') }}</label>
        <select v-model="kind" class="select w-40">
          <option value="catalog">{{ t('search.kind.catalog') }}</option>
          <option value="knowledge">{{ t('search.kind.knowledge') }}</option>
          <option value="both">{{ t('search.kind.both') }}</option>
        </select>
      </div>
      <label class="flex items-center gap-2 text-sm pb-2">
        <input v-model="refine" type="checkbox" class="checkbox" />
        {{ t('search.refine') }}
      </label>
      <button type="submit" class="btn-primary" :disabled="busy || !query.trim()">
        <UiIcon name="search" :size="16" /> {{ t('search.find') }}
      </button>
    </form>

    <div v-if="result" class="space-y-4">
      <div v-for="group in groups" :key="group.key">
        <h3 class="text-sm font-semibold mb-1">
          {{ t(`search.kind.${group.key}`) }}
          <span class="muted font-normal">· {{ t('search.found', { n: group.items.length }) }}</span>
          <span v-if="group.kept" class="muted font-normal">· {{ t('search.kept', { n: group.kept.length }) }}</span>
        </h3>
        <p v-if="!group.items.length" class="text-sm muted">{{ t('search.nothing') }}</p>
        <div v-else class="overflow-x-auto">
          <table class="table text-sm">
            <thead>
              <tr>
                <th class="w-10">#</th>
                <th>{{ t('search.result_title') }}</th>
                <th v-if="group.key === 'catalog'">{{ t('catalog.price') }}</th>
                <th v-if="result.refined">{{ t('search.match') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(f, i) in group.items" :key="f.id" :class="keptIds.has(f.id) ? 'bg-emerald-50/60 dark:bg-emerald-900/10' : ''">
                <td class="muted">{{ i + 1 }}</td>
                <td>
                  <div class="font-medium">{{ f.title }}</div>
                  <div class="text-xs muted line-clamp-2">{{ f.text }}</div>
                  <div class="text-xs muted code">{{ f.ref }}<span v-if="f.category"> · {{ f.category }}</span></div>
                </td>
                <td v-if="group.key === 'catalog'" class="whitespace-nowrap">{{ f.price ? `${f.price} ${f.currency || ''}` : '—' }}</td>
                <td v-if="result.refined" class="whitespace-nowrap">
                  <span :class="keptIds.has(f.id) ? 'badge-green' : 'badge-gray'">{{ f.match == null ? '—' : num(f.match, 2) }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <details v-if="result.notes?.length" class="text-sm">
        <summary class="cursor-pointer muted">{{ t('search.notes') }}</summary>
        <JsonView :value="result.notes" />
      </details>
    </div>
  </div>
</template>

<script setup lang="ts">
// "What would the bot find": POST …/search/test, with or without the Jev check.
const props = defineProps<{ defaultKind: 'catalog' | 'knowledge' | 'both' }>()
const { t } = useI18n()
const api = useApi()
const { path } = useTenant()
const { num } = useFormat()
const { busy, run } = useAction()

const query = ref('')
const kind = ref(props.defaultKind)
const refine = ref(true)
const result = ref<any>(null)

const keptIds = computed(() => {
  const r = result.value?.refined
  if (!r) return new Set<string>()
  return new Set<string>([...r.catalog, ...r.knowledge].map((f: any) => f.id))
})

const groups = computed(() => {
  const r = result.value
  if (!r) return []
  const out: { key: 'catalog' | 'knowledge'; items: any[]; kept: any[] | null }[] = []
  if (kind.value !== 'knowledge') out.push({ key: 'catalog', items: r.catalog, kept: r.refined?.catalog || null })
  if (kind.value !== 'catalog') out.push({ key: 'knowledge', items: r.knowledge, kept: r.refined?.knowledge || null })
  return out
})

async function search() {
  const res = await run(() =>
    api<any>(path('/search/test'), {
      method: 'POST',
      body: { query: query.value.trim(), kind: kind.value, refine: refine.value },
    }),
  )
  if (res) result.value = res
}
</script>
