<template>
  <button
    type="button"
    :aria-label="isDark ? t('common.theme_light') : t('common.theme_dark')"
    :title="isDark ? t('common.theme_light') : t('common.theme_dark')"
    class="h-9 w-9 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors flex items-center justify-center"
    @click="toggle"
  >
    <UiIcon :name="isDark ? 'sun' : 'moon'" :size="16" />
  </button>
</template>

<script setup lang="ts">
const { t } = useI18n()
const isDark = ref(false)

onMounted(() => {
  isDark.value = document.documentElement.classList.contains('dark')
})

function toggle() {
  isDark.value = !isDark.value
  document.documentElement.classList.toggle('dark', isDark.value)
  try {
    localStorage.setItem('theme', isDark.value ? 'dark' : 'light')
  } catch {
    // storage blocked: the choice lasts until reload
  }
}
</script>
