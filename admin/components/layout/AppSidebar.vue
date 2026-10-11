<template>
  <nav
    class="h-screen sticky top-0 flex flex-col bg-slate-900 text-[#c5cbe3] border-r border-slate-900 dark:border-slate-700 transition-[width] duration-200"
    :class="collapsed ? 'w-[76px]' : 'w-[248px]'"
  >
    <!-- navy in both themes: daily work on top, everything set up once behind «Settings» -->
    <!-- logo -->
    <div class="h-16 shrink-0 flex items-center gap-2.5 px-4">
      <span class="grid place-items-center w-9 h-9 shrink-0 rounded-button bg-secondary text-slate-900 font-bold">Z</span>
      <span v-if="!collapsed" class="font-bold text-white truncate">Zama Chat</span>
    </div>

    <!-- tenant -->
    <div v-if="auth.tenants.length" class="px-3 pb-1">
      <TenantSwitcher :collapsed="collapsed" />
    </div>

    <div class="flex-1 overflow-y-auto px-3 py-3 space-y-5">
      <div v-for="group in groups" :key="group.key">
        <template v-if="group.label">
          <p v-if="!collapsed" class="px-3 mb-1.5 text-xs font-semibold uppercase tracking-wider text-slate-400">
            {{ t(`nav.group.${group.label}`) }}
          </p>
          <div v-else class="mx-3 mb-2 border-t border-slate-700" />
        </template>
        <ul class="space-y-1">
          <li v-for="item in group.items" :key="item.to">
            <NuxtLink
              :to="item.to"
              :title="collapsed ? t(`nav.${item.key}`) : undefined"
              class="relative flex items-center gap-3 h-11 px-3 rounded-button text-[15px] transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
              :class="item.active ? 'bg-slate-700 text-white font-semibold' : 'hover:bg-slate-800 hover:text-white'"
              :aria-current="item.active ? 'page' : undefined"
              @click="emit('navigate')"
            >
              <UiIcon :name="item.icon" />
              <span v-if="!collapsed" class="truncate">{{ t(`nav.${item.key}`) }}</span>
              <span
                v-if="item.count"
                class="grid place-items-center min-w-[22px] h-[22px] px-1.5 rounded-full bg-secondary text-slate-900 text-xs font-bold"
                :class="collapsed ? 'absolute -top-1 -right-1' : 'ml-auto'"
                :title="t('nav.waiting', { n: item.count })"
              >
                {{ item.count > 99 ? '99+' : item.count }}
              </span>
            </NuxtLink>
          </li>
        </ul>
      </div>
    </div>

    <div class="p-3 space-y-1 border-t border-slate-800">
      <NuxtLink
        to="/docs"
        :title="collapsed ? t('nav.docs') : undefined"
        class="flex items-center gap-3 h-11 px-3 rounded-button text-[15px] transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        :class="inDocs ? 'bg-slate-700 text-white font-semibold' : 'hover:bg-slate-800 hover:text-white'"
        :aria-current="inDocs ? 'page' : undefined"
        @click="emit('navigate')"
      >
        <UiIcon name="docs" />
        <span v-if="!collapsed" class="truncate">{{ t('nav.docs') }}</span>
      </NuxtLink>
      <NuxtLink
        :to="hubLink"
        :title="collapsed ? t('nav.settings') : undefined"
        class="relative flex items-center gap-3 h-11 px-3 rounded-button text-[15px] transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        :class="inSettings ? 'bg-slate-700 text-white font-semibold' : 'hover:bg-slate-800 hover:text-white'"
        :aria-current="inSettings ? 'page' : undefined"
        @click="emit('navigate')"
      >
        <UiIcon name="settings" />
        <span v-if="!collapsed" class="truncate">{{ t('nav.settings') }}</span>
        <span
          v-if="roleProblems > 0"
          class="w-2.5 h-2.5 rounded-full bg-rose-500"
          :class="collapsed ? 'absolute top-1.5 right-1.5' : 'ml-auto'"
          :title="t('models.roles.problem_banner')"
        />
      </NuxtLink>
      <button
        v-if="!mobile"
        type="button"
        class="flex items-center gap-3 w-full h-11 px-3 rounded-button text-[15px] text-slate-400 hover:bg-slate-800 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        @click="emit('toggle')"
      >
        <UiIcon :name="collapsed ? 'chevrons-right' : 'chevrons-left'" />
        <span v-if="!collapsed">{{ t('nav.collapse') }}</span>
      </button>
    </div>
  </nav>
</template>

<script setup lang="ts">
import { useAuthStore } from '~/stores/auth'

defineProps<{ collapsed?: boolean; mobile?: boolean }>()
const emit = defineEmits<{ toggle: []; navigate: [] }>()

const { t } = useI18n()
const route = useRoute()
const auth = useAuthStore()
const { base, can } = useTenant()
const { hubLink, inSettings } = useNav()
const roleProblems = useRoleProblems()
const waiting = useInboxCount()

interface Item {
  key: string
  to: string
  icon: string
  count?: number
  active?: boolean
}

// the documentation is one for every tenant: /docs
const inDocs = computed(() => isActive('/docs'))

function isActive(to: string) {
  return route.path === to || route.path.startsWith(`${to}/`)
}

const groups = computed(() => {
  const out: { key: string; label?: string; items: Item[] }[] = []
  const b = base.value
  if (!b) return out
  const work: Item[] = [
    { key: 'inbox', to: `${b}/inbox`, icon: 'inbox', count: waiting.value },
    { key: 'scenarios', to: `${b}/scenarios`, icon: 'scenarios' },
  ]
  if (can('admin')) work.push({ key: 'sandbox', to: `${b}/sandbox`, icon: 'sandbox' })
  work.push({ key: 'results', to: `${b}/results`, icon: 'results' })
  out.push({ key: 'work', items: work })
  out.push({
    key: 'knows',
    label: 'knows',
    items: [
      { key: 'catalog', to: `${b}/catalog`, icon: 'catalog' },
      { key: 'knowledge', to: `${b}/knowledge`, icon: 'knowledge' },
    ],
  })
  for (const g of out) for (const item of g.items) item.active = isActive(item.to)
  return out
})
</script>
