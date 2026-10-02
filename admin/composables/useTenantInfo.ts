// The tenant's settings and channels (GET /tenants/<id>), kept per tenant for the tab:
// filters and forms on several pages need the channel list and the bot languages.
const cache = reactive<Record<string, any>>({})

export function useTenantInfo() {
  const { tenantId, path } = useTenant()
  const api = useApi()
  const info = computed(() => cache[tenantId.value] || null)
  const channels = computed(() => (info.value?.channels || []) as any[])
  const languages = computed(() => (info.value?.effective_settings?.languages || []) as string[])

  async function load(force = false) {
    const id = tenantId.value
    if (!id) return null
    if (!force && cache[id]) return cache[id]
    cache[id] = await api(path())
    return cache[id]
  }
  return { info, channels, languages, load }
}
