<template>
  <header
    class="h-16 shrink-0 flex items-center gap-3 px-4 md:px-6 bg-white dark:bg-slate-800 border-b border-slate-200 dark:border-slate-700 sticky top-0 z-30"
  >
    <button
      v-if="showHamburger"
      type="button"
      class="lg:hidden btn-icon btn-ghost"
      :aria-label="t('nav.menu')"
      @click="emit('menu')"
    >
      <UiIcon name="menu" />
    </button>

    <nav class="flex-1 min-w-0 flex items-center gap-1.5 text-sm" aria-label="breadcrumbs">
      <template v-for="(c, i) in crumbs" :key="i">
        <UiIcon v-if="i > 0" name="chevron-right" :size="14" class="text-slate-400" />
        <NuxtLink
          v-if="c.to && i < crumbs.length - 1"
          :to="c.to"
          class="truncate text-slate-500 hover:text-slate-900 hover:underline dark:text-slate-400 dark:hover:text-slate-200"
        >
          {{ c.label }}
        </NuxtLink>
        <span v-else class="truncate font-semibold text-slate-900 dark:text-white">{{ c.label }}</span>
      </template>
    </nav>

    <LanguageSwitcher />
    <ThemeToggle />
    <UserMenu />
  </header>
</template>

<script setup lang="ts">
defineProps<{ showHamburger?: boolean }>()
const emit = defineEmits<{ menu: [] }>()
const { t } = useI18n()
const pageCrumbs = useCrumbs()
const { hubLink, inSettings, onHub } = useNav()

// a page behind «Settings» shows the way back to it
const crumbs = computed(() =>
  inSettings.value && !onHub.value ? [{ label: t('nav.settings'), to: hubLink.value }, ...pageCrumbs.value] : pageCrumbs.value,
)
</script>
