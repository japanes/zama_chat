// `$api`: $fetch bound to the backend's admin API through the /api proxy.
// Every failure becomes an ApiError {code, params, status} — the backend sends codes
// with params, never ready text, and `useErrorText()` turns them into the user's language.
// 401 (no session / session expired) sends the user to /login and back afterwards.
import { useAuthStore } from '~/stores/auth'

export class ApiError extends Error {
  code: string
  params: Record<string, unknown>
  status: number

  constructor(code: string, params: Record<string, unknown> = {}, status = 0) {
    super(code)
    this.code = code
    this.params = params
    this.status = status
  }
}

export function toApiError(e: any): ApiError {
  if (e instanceof ApiError) return e
  const status = e?.response?.status ?? e?.statusCode ?? 0
  const body = e?.data?.error
  if (body?.code) return new ApiError(body.code, body.params || {}, status)
  if (!status) return new ApiError('client.network', {}, 0)
  return new ApiError('client.http', { status }, status)
}

export default defineNuxtPlugin(() => {
  const router = useRouter()
  const auth = useAuthStore()

  const raw = $fetch.create({
    baseURL: '/api/v1/admin',
    credentials: 'same-origin',
    retry: 0,
  })

  async function api<T = any>(path: string, opts: Record<string, any> = {}): Promise<T> {
    try {
      return (await raw(path, opts as any)) as T
    } catch (e: any) {
      const err = toApiError(e)
      if (err.status === 401 && !opts.noAuthRedirect) {
        auth.clear()
        const here = router.currentRoute.value
        if (!here.meta.public) {
          await router.push({ path: '/login', query: { next: here.fullPath } })
        }
      }
      throw err
    }
  }

  return { provide: { api } }
})
