// How many LLM roles would fail a turn right now (no model / model switched off) for the
// current tenant. Shown as a red dot in the menu and a red banner on the roles page.
import { useAuthStore } from '~/stores/auth'

const count = ref(0)
let loadedFor: string | null = null

export function useRoleProblems() {
  return count
}

export async function refreshRoleProblems(tenantId: string | null, force = false) {
  const auth = useAuthStore()
  if (!auth.isAnyAdmin) return
  const key = tenantId || '-'
  if (!force && loadedFor === key) return
  loadedFor = key
  const { $api } = useNuxtApp()
  try {
    const canTenant = tenantId && auth.can(tenantId, 'admin')
    const res = await $api<{ items: { problem: string | null }[] }>('/roles', {
      query: canTenant ? { tenant_id: tenantId } : {},
    })
    count.value = res.items.filter((r) => r.problem).length
  } catch {
    count.value = 0
  }
}
