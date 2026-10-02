<template>
  <div ref="root" class="relative">
    <button
      type="button"
      class="h-9 px-2.5 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-sm font-medium text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors flex items-center gap-1.5"
      :aria-label="t('common.language')"
      @click="open = !open"
    >
      {{ current?.name || locale }}
      <UiIcon name="chevron-down" :size="14" class="text-slate-400" />
    </button>
    <div
      v-if="open"
      class="absolute right-0 mt-1 w-36 bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 shadow-lg py-1 z-50"
    >
      <button
        v-for="loc in list"
        :key="loc.code"
        type="button"
        class="w-full px-3 py-1.5 text-left text-sm hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
        :class="loc.code === locale ? 'text-primary font-medium' : 'text-slate-700 dark:text-slate-300'"
        @click="pick(loc.code)"
      >
        {{ loc.name }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
const { t } = useI18n()
const { locale, locales, switchTo } = useLocaleSwitch()
const open = ref(false)
const root = ref<HTMLElement | null>(null)

const list = computed(() => locales.value as { code: string; name: string }[])
const current = computed(() => list.value.find((l) => l.code === locale.value))

async function pick(code: string) {
  open.value = false
  await switchTo(code)
}

onClickOutside(root, () => (open.value = false))
</script>
