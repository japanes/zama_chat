<template>
  <Teleport to="body">
    <Transition name="modal">
      <div
        v-if="open"
        class="fixed inset-0 z-[100] flex items-start sm:items-center justify-center bg-slate-900/50 p-4 overflow-y-auto"
        role="dialog"
        aria-modal="true"
        @click.self="close"
        @keydown.escape="close"
      >
        <div
          class="w-full rounded-modal bg-white dark:bg-slate-800 shadow-modal border border-slate-200 dark:border-slate-700 my-8"
          :class="width"
        >
          <div class="flex items-center justify-between gap-3 px-5 py-4 border-b border-slate-200 dark:border-slate-700">
            <h3 class="text-base font-semibold text-slate-900 dark:text-white truncate">{{ title }}</h3>
            <button type="button" class="btn-icon btn-ghost -mr-2" :aria-label="t('common.close')" @click="close">
              <UiIcon name="close" :size="18" />
            </button>
          </div>
          <div class="px-5 py-4">
            <slot />
          </div>
          <div v-if="$slots.footer" class="flex flex-wrap justify-end gap-2 px-5 py-4 border-t border-slate-200 dark:border-slate-700">
            <slot name="footer" />
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
const props = withDefaults(defineProps<{ open: boolean; title?: string; size?: 'sm' | 'md' | 'lg' | 'xl' }>(), {
  title: '',
  size: 'md',
})
const emit = defineEmits<{ close: [] }>()
const { t } = useI18n()
const width = computed(
  () => ({ sm: 'max-w-sm', md: 'max-w-lg', lg: 'max-w-3xl', xl: 'max-w-5xl' })[props.size],
)
function close() {
  emit('close')
}
</script>

<style scoped>
.modal-enter-active,
.modal-leave-active {
  transition: opacity 0.18s ease;
}
.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}
</style>
