// How many conversations wait for a person right now (the badge next to «Inbox»).
// One number per tab; the layout refreshes it, the inbox page sets it from its own list.
const waiting = ref(0)

export function useInboxCount() {
  return waiting
}

export async function refreshInboxCount(tenantId: string | null) {
  if (!tenantId) {
    waiting.value = 0
    return
  }
  const { $api } = useNuxtApp()
  try {
    const res = await $api<{ waiting: number }>(`/tenants/${tenantId}/inbox/counts`)
    waiting.value = res.waiting
  } catch {
    // the badge is a hint: a failed refresh keeps the last number
  }
}
