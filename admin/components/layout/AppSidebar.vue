<template>
  <nav
    class="h-screen sticky top-0 flex flex-col bg-white dark:bg-slate-800 border-r border-slate-200 dark:border-slate-700 transition-[width] duration-200"
    :class="collapsed ? 'w-[72px]' : 'w-[260px]'"
  >
    <!-- logo -->
    <div class="h-16 shrink-0 flex items-center gap-2.5 px-4 border-b border-slate-200 dark:border-slate-700">
      <span class="grid place-items-center w-9 h-9 rounded-button bg-gradient-to-br from-secondary to-primary text-white font-heading font-bold">
        Z
      </span>
      <span v-if="!collapsed" class="font-heading font-bold text-slate-900 dark:text-white truncate">
        Zama Chat
      </span>
    </div>

    <!-- tenant -->
    <div v-if="auth.tenants.length" class="px-3 pt-3">
      <TenantSwitcher :collapsed="collapsed" />
    </div>

    <div class="flex-1 overflow-y-auto px-3 py-3 space-y-5">
      <div v-for="group in groups" :key="group.key">
        <p
          v-if="!collapsed"
          class="px-3 mb-1.5 text-xs font-medium uppercase tracking-wider text-slate-400"
        >
          {{ t(`nav.group.${group.key}`) }}
        </p>
        <div v-else class="mx-3 mb-1.5 border-t border-slate-200 dark:border-slate-700" />
        <ul class="space-y-0.5">
          <li v-for="item in group.items" :key="item.to">
            <NuxtLink
              :to="item.to"
              :title="collapsed ? t(`nav.${item.key}`) : undefined"
              class="flex items-center gap-3 h-10 px-3 rounded-button text-sm transition-colors"
              :class="isActive(item.to)
                ? 'bg-primary/10 text-primary font-medium'
                : 'text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700'"
              @click="emit('navigate')"
            >
              <UiIcon :name="item.icon" />
              <span v-if="!collapsed" class="truncate">{{ t(`nav.${item.key}`) }}</span>
              <span
                v-if="item.alert"
                class="ml-auto w-2 h-2 rounded-full bg-rose-500"
                :title="t('models.roles.problem_banner')"
              />
            </NuxtLink>
          </li>
        </ul>
      </div>
    </div>

    <div v-if="!mobile" class="p-3 border-t border-slate-200 dark:border-slate-700">
      <button
        type="button"
        class="flex items-center gap-3 w-full h-10 px-3 rounded-button text-sm text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-700"
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
const roleProblems = useRoleProblems()

interface Item {
  key: string
  to: string
  icon: string
  alert?: boolean
}

const groups = computed(() => {
  const out: { key: string; items: Item[] }[] = []
  if (base.value) {
    const b = base.value
    const activity: Item[] = [
      { key: 'results', to: `${b}/results`, icon: 'results' },
      { key: 'conversations', to: `${b}/conversations`, icon: 'chat' },
      { key: 'operator', to: `${b}/operator`, icon: 'inbox' },
    ]
    if (can('admin')) activity.push({ key: 'sandbox', to: `${b}/sandbox`, icon: 'sandbox' })
    out.push({ key: 'activity', items: activity })

    const bot: Item[] = [
      { key: 'scenarios', to: `${b}/scenarios`, icon: 'scenarios' },
      { key: 'channels', to: `${b}/channels`, icon: 'channels' },
    ]
    if (can('admin')) {
      bot.push({ key: 'notifiers', to: `${b}/notifiers`, icon: 'bell' })
      bot.push({ key: 'prompts', to: `${b}/prompts`, icon: 'prompts' })
      bot.push({ key: 'settings', to: `${b}/settings`, icon: 'settings' })
    }
    out.push({ key: 'bot', items: bot })

    if (can('admin')) {
      const access: Item[] = [
        { key: 'members', to: `${b}/members`, icon: 'members' },
        { key: 'api_keys', to: `${b}/api-keys`, icon: 'key' },
        { key: 'endpoints', to: `${b}/endpoints`, icon: 'endpoints' },
      ]
      if (can('owner')) access.push({ key: 'audit', to: `${b}/audit`, icon: 'audit' })
      out.push({ key: 'access', items: access })
    }
  }
  if (auth.isAnyAdmin) {
    out.push({
      key: 'models',
      items: [
        { key: 'models', to: '/models', icon: 'models' },
        { key: 'roles', to: '/models/roles', icon: 'settings', alert: roleProblems.value > 0 },
        { key: 'costs', to: '/models/costs', icon: 'costs' },
      ],
    })
  }
  if (auth.isSuper) {
    out.push({
      key: 'system',
      items: [
        { key: 'tenants', to: '/system/tenants', icon: 'tenants' },
        { key: 'users', to: '/system/users', icon: 'members' },
        { key: 'shared_prompts', to: '/system/prompts', icon: 'prompts' },
        { key: 'system_audit', to: '/system/audit', icon: 'audit' },
      ],
    })
  }
  return out
})

// /models is active only on itself; deeper links match their own subtree
function isActive(to: string) {
  if (to === '/models') return route.path === '/models'
  return route.path === to || route.path.startsWith(`${to}/`)
}
</script>
