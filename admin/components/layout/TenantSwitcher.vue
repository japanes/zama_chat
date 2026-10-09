<template>
  <div ref="root" class="relative">
    <button
      type="button"
      class="w-full flex items-center gap-2.5 h-12 px-2.5 rounded-button border border-slate-700 bg-slate-800 text-white hover:bg-slate-700 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
      :title="tenant?.title"
      @click="open = !open"
    >
      <span class="grid place-items-center w-7 h-7 shrink-0 rounded-lg bg-slate-700 text-white text-sm font-semibold">
        {{ (tenant?.title || '?').charAt(0).toUpperCase() }}
      </span>
      <template v-if="!collapsed">
        <span class="flex-1 min-w-0 text-left">
          <span class="block text-sm font-semibold truncate">{{ tenant?.title || t('nav.pick_tenant') }}</span>
          <span v-if="tenant" class="block text-xs text-slate-400 truncate">{{ t(`roles.${tenant.role}`) }}</span>
        </span>
        <UiIcon name="chevron-down" :size="14" class="text-slate-400" />
      </template>
    </button>
    <div
      v-if="open"
      class="absolute left-0 mt-1 w-[236px] max-h-80 overflow-y-auto bg-white dark:bg-slate-800 rounded-card border border-slate-200 dark:border-slate-700 shadow-lg py-1 z-50"
    >
      <button
        v-for="item in auth.tenants"
        :key="item.id"
        type="button"
        class="w-full flex items-center justify-between gap-2 px-3 py-2.5 text-left text-sm hover:bg-slate-100 dark:hover:bg-slate-700"
        :class="item.id === tenant?.id ? 'text-primary-dark dark:text-primary-light font-semibold' : 'text-slate-900 dark:text-slate-300'"
        @click="pick(item.slug)"
      >
        <span class="truncate">{{ item.title }}</span>
        <span class="badge-gray">{{ item.slug }}</span>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useAuthStore } from '~/stores/auth'

defineProps<{ collapsed?: boolean }>()
const { t } = useI18n()
const auth = useAuthStore()
const route = useRoute()
const { tenant } = useTenant()
const open = ref(false)
const root = ref<HTMLElement | null>(null)

// the same section in the other tenant (/t/a/results → /t/b/results); a detail page
// (an id of the old tenant) goes to its list
function pick(slug: string) {
  open.value = false
  rememberTenant(slug)
  const m = route.path.match(/^\/t\/[^/]+\/([^/]+)/)
  navigateTo(`/t/${slug}/${m ? m[1] : 'inbox'}`)
}

onClickOutside(root, () => (open.value = false))
</script>
