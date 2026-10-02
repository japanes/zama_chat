import { defineStore } from 'pinia'

export type TenantRole = 'operator' | 'admin' | 'owner'

export interface MeTenant {
  id: string
  slug: string
  title: string
  role: TenantRole
  is_active: boolean
}

export interface MeUser {
  id: string
  email: string
  name: string | null
  avatar_url: string | null
  is_superadmin: boolean
  locale: string | null
  roles: Record<string, TenantRole>
}

const RANK: Record<TenantRole, number> = { operator: 1, admin: 2, owner: 3 }

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: null as MeUser | null,
    tenants: [] as MeTenant[],
    loaded: false,
  }),
  getters: {
    isSuper: (s) => !!s.user?.is_superadmin,
    // superadmin or admin / owner somewhere: may open the models page
    isAnyAdmin: (s) =>
      !!s.user?.is_superadmin || s.tenants.some((t) => RANK[t.role] >= RANK.admin),
  },
  actions: {
    async fetchMe() {
      const { $api } = useNuxtApp()
      try {
        const res = await $api<{ user: MeUser; tenants: MeTenant[] }>('/auth/me', {
          noAuthRedirect: true,
        })
        this.user = res.user
        this.tenants = res.tenants
      } catch {
        this.user = null
        this.tenants = []
      }
      this.loaded = true
      return this.user
    },
    clear() {
      this.user = null
      this.tenants = []
      this.loaded = true
    },
    async logout() {
      const { $api } = useNuxtApp()
      try {
        await $api('/auth/logout', { method: 'POST', noAuthRedirect: true })
      } finally {
        this.clear()
        await navigateTo('/login')
      }
    },
    roleIn(tenantId: string): TenantRole | null {
      if (this.user?.is_superadmin) return 'owner'
      return this.tenants.find((t) => t.id === tenantId)?.role ?? null
    },
    can(tenantId: string, need: TenantRole): boolean {
      const role = this.roleIn(tenantId)
      return !!role && RANK[role] >= RANK[need]
    },
  },
})
