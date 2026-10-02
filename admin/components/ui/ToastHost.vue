<template>
  <Teleport to="body">
    <div class="fixed bottom-4 right-4 z-[120] flex flex-col gap-2 w-[calc(100%-2rem)] max-w-sm" aria-live="polite">
      <TransitionGroup name="toast">
        <div
          v-for="item in toasts"
          :key="item.id"
          class="flex items-start gap-2 rounded-card border px-4 py-3 text-sm shadow-card-hover bg-white dark:bg-slate-800"
          :class="{
            'border-emerald-300 dark:border-emerald-500/40': item.tone === 'ok',
            'border-rose-300 dark:border-rose-500/40': item.tone === 'error',
            'border-slate-200 dark:border-slate-700': item.tone === 'info',
          }"
        >
          <UiIcon
            :name="item.tone === 'error' ? 'alert' : 'check'"
            :size="18"
            :class="item.tone === 'error' ? 'text-rose-500' : 'text-emerald-500'"
          />
          <span class="flex-1 break-words">{{ item.text }}</span>
          <button type="button" class="text-slate-400 hover:text-slate-600" @click="dismiss(item.id)">
            <UiIcon name="close" :size="16" />
          </button>
        </div>
      </TransitionGroup>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
const { toasts, dismiss } = useToast()
</script>

<style scoped>
.toast-enter-active,
.toast-leave-active {
  transition: all 0.2s ease;
}
.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateY(8px);
}
</style>
