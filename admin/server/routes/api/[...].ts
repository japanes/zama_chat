// /api/v1/admin/** → the backend's /v1/admin/**. Nothing else goes through: the public
// API (/v1/messages) and dev routes are not the admin's business.
import { joinURL } from 'ufo'

export default defineEventHandler((event) => {
  const path = event.path.replace(/^\/api/, '')
  if (!path.startsWith('/v1/admin/')) {
    throw createError({ statusCode: 404, statusMessage: 'Not found' })
  }
  const { backendUrl } = useRuntimeConfig(event)
  return proxyRequest(event, joinURL(backendUrl, path))
})
