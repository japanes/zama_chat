<template>
  <Teleport to="body">
    <Transition name="confirm">
      <!-- Escape stops here: a modal underneath must not close on the same key -->
      <div
        v-if="state.open"
        class="fixed inset-0 z-[110] grid place-items-center bg-slate-900/50 px-4"
        role="dialog"
        aria-modal="true"
        @click.self="cancel"
        @keydown.escape.stop="cancel"
      >
        <div class="w-full max-w-md rounded-modal bg-white dark:bg-slate-800 shadow-modal border border-slate-200 dark:border-slate-700 p-5">
          <div class="flex items-start gap-3">
            <span
              class="shrink-0 grid place-items-center w-10 h-10 rounded-xl"
              :class="danger ? 'bg-rose-100 dark:bg-rose-500/15 text-rose-600 dark:text-rose-400' : 'bg-primary/10 text-primary'"
            >
              <UiIcon name="alert" />
            </span>
            <div class="min-w-0">
              <h3 class="text-base font-semibold text-slate-900 dark:text-white">{{ o.title || t('common.confirm_title') }}</h3>
              <p v-if="o.message" class="mt-1 text-sm text-slate-500 dark:text-slate-400 whitespace-pre-line break-words">{{ o.message }}</p>
            </div>
          </div>
          <div class="mt-5 flex justify-end gap-2">
            <button v-if="!o.hideCancel" ref="cancelBtn" type="button" class="btn-ghost" @click="cancel">
              {{ o.cancelLabel || t('common.cancel') }}
            </button>
            <button ref="okBtn" type="button" :class="danger ? 'btn-danger' : 'btn-primary'" @click="accept">
              {{ o.confirmLabel || t('common.confirm') }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
const { t } = useI18n()
const { state, accept, cancel } = useConfirm()
const o = computed(() => state.value.opts || {})
const danger = computed(() => o.value.tone === 'danger')

const cancelBtn = ref<HTMLButtonElement | null>(null)
const okBtn = ref<HTMLButtonElement | null>(null)
watch(
  () => state.value.open,
  (open) => {
    if (open) nextTick(() => (cancelBtn.value || okBtn.value)?.focus())
  },
)
</script>

<style scoped>
.confirm-enter-active,
.confirm-leave-active {
  transition: opacity 0.18s ease;
}
.confirm-enter-from,
.confirm-leave-to {
  opacity: 0;
}
</style>
