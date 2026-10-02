// The tenant of the page: /t/<slug>/… . Pages outside a tenant (models, profile, system)
// use the last one opened, so the sidebar keeps its tenant links.
import { useAuthStore, type MeTenant, type TenantRole } from '~/stores/auth'

const LAST_KEY = 'zama_last_tenant'

export function rememberTenant(slug: string) {
  try {
    localStorage.setItem(LAST_KEY, slug)
  } catch {
    // storage blocked
  }
}

export function lastTenantSlug(): string | null {
  try {
    return localStorage.getItem(LAST_KEY)
  } catch {
    return null
  }
}

export function useTenant() {
  const route = useRoute()
  const auth = useAuthStore()

  const tenant = computed<MeTenant | null>(() => {
    const slug = (route.params.tenant as string | undefined) || lastTenantSlug()
    return (
      auth.tenants.find((t) => t.slug === slug) ||
      (route.params.tenant ? null : auth.tenants[0] || null)
    )
  })
  const tenantId = computed(() => tenant.value?.id || '')
  const base = computed(() => (tenant.value ? `/t/${tenant.value.slug}` : ''))

  function can(need: TenantRole) {
    return !!tenant.value && auth.can(tenant.value.id, need)
  }
  // /v1/admin/tenants/<id><path>
  function path(p = '') {
    return `/tenants/${tenantId.value}${p}`
  }
  return { tenant, tenantId, base, can, path }
}
