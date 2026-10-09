<template>
  <section class="card overflow-hidden">
    <!-- one numbered step of the scenario editor: the header says what is set, the body
         opens under it -->
    <h2>
      <button
        type="button"
        class="flex items-center gap-4 w-full px-5 py-[18px] text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary"
        :aria-expanded="open"
        :aria-controls="`editor-step-${n}`"
        @click="emit('toggle')"
      >
        <span class="grid place-items-center w-9 h-9 shrink-0 rounded-full bg-[#141b3b] dark:bg-slate-600 text-white font-semibold">{{ n }}</span>
        <span class="flex-1 min-w-0">
          <span class="block text-[17px] font-semibold text-slate-900 dark:text-white">{{ title }}</span>
          <span class="block text-sm muted">{{ summary }}</span>
        </span>
        <span v-if="problems?.length" class="badge-red shrink-0">{{ t('editor.to_fix', { n: problems.length }) }}</span>
        <UiIcon :name="open ? 'chevron-up' : 'chevron-down'" :size="20" class="shrink-0 muted" />
      </button>
    </h2>
    <div v-show="open" :id="`editor-step-${n}`" class="px-5 pb-[22px] pt-0.5 space-y-3">
      <ul v-if="problems?.length" class="alert-red space-y-1" role="alert">
        <li v-for="(p, i) in problems" :key="i">{{ p }}</li>
      </ul>
      <slot />
    </div>
  </section>
</template>

<script setup lang="ts">
defineProps<{ n: number; title: string; summary: string; open: boolean; problems?: string[] }>()
const emit = defineEmits<{ toggle: [] }>()
const { t } = useI18n()
</script>
