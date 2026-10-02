// Loading a page's data: `data`, `loading`, `error` (already in the user's language).
export function useLoader<T>(fn: () => Promise<T>) {
  const { errorText } = useErrorText()
  const data = ref<T | null>(null) as Ref<T | null>
  const loading = ref(false)
  const error = ref<string | null>(null)
  async function load() {
    loading.value = true
    error.value = null
    try {
      data.value = await fn()
    } catch (e) {
      error.value = errorText(e)
    } finally {
      loading.value = false
    }
    return data.value
  }
  return { data, loading, error, load }
}

// A button's action: busy flag, a toast with the error text, an optional "done" toast.
export function useAction() {
  const busy = ref(false)
  const toast = useToast()
  const { errorText } = useErrorText()
  async function run<T>(fn: () => Promise<T>, okText?: string): Promise<T | undefined> {
    busy.value = true
    try {
      const result = await fn()
      if (okText) toast.ok(okText)
      return result
    } catch (e) {
      toast.error(errorText(e))
      return undefined
    } finally {
      busy.value = false
    }
  }
  return { busy, run }
}
