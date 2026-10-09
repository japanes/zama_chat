// The menu is short: daily work in the sidebar, everything set up once behind «Settings».
// One list of the settings pages for the hub page, the sidebar and the breadcrumbs.
import { useAuthStore } from '~/stores/auth'

export interface HubItem {
  key: string
  to: string
  icon: string
  alert?: boolean
}
export interface HubGroup {
  key: string
  items: HubItem[]
}

// pages of a tenant that live behind «Settings»
const TENANT_SECTIONS = ['channels', 'notifiers', 'endpoints', 'api-keys', 'settings', 'prompts', 'decisions', 'members', 'audit']

export function useNav() {
  const route = useRoute()
  const auth = useAuthStore()
  const { base, can } = useTenant()
  const roleProblems = useRoleProblems()

  const hubLink = computed(() => (base.value ? `${base.value}/settings` : '/settings'))

  const hubGroups = computed<HubGroup[]>(() => {
    const out: HubGroup[] = []
    const b = base.value
    if (b) {
      const connect: HubItem[] = [{ key: 'channels', to: `${b}/channels`, icon: 'channels' }]
      if (can('admin')) {
        connect.push({ key: 'notifiers', to: `${b}/notifiers`, icon: 'bell' })
        connect.push({ key: 'endpoints', to: `${b}/endpoints`, icon: 'endpoints' })
        connect.push({ key: 'api_keys', to: `${b}/api-keys`, icon: 'key' })
      }
      out.push({ key: 'connect', items: connect })
      if (can('admin')) {
        out.push({
          key: 'behaviour',
          items: [
            { key: 'bot_settings', to: `${b}/settings/bot`, icon: 'settings' },
            { key: 'prompts', to: `${b}/prompts`, icon: 'prompts' },
            { key: 'decisions', to: `${b}/decisions`, icon: 'decisions' },
          ],
        })
        const access: HubItem[] = [{ key: 'members', to: `${b}/members`, icon: 'members' }]
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

  // is the page one of those behind «Settings» (the hub itself included)
  const inSettings = computed(() => {
    const p = route.path
    if (p === '/settings' || p.startsWith('/models') || p.startsWith('/system')) return true
    const m = p.match(/^\/t\/[^/]+\/([^/]+)/)
    return !!m && TENANT_SECTIONS.includes(m[1])
  })
  const onHub = computed(() => route.path === hubLink.value)

  return { hubLink, hubGroups, inSettings, onHub }
}
