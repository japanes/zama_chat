// Every page needs a session except the ones marked `public` (login, email link).
// The user is loaded once per tab; the interface language follows users.locale.
import { useAuthStore } from '~/stores/auth'

export default defineNuxtRouteMiddleware(async (to) => {
  const auth = useAuthStore()
  if (!auth.loaded) {
    await auth.fetchMe()
    if (auth.user?.locale) await applyUserLocale(auth.user.locale)
  }
  if (to.meta.public) {
    if (auth.user && to.path === '/login') return navigateTo('/')
    return
  }
  if (!auth.user) {
    return navigateTo({ path: '/login', query: to.fullPath !== '/' ? { next: to.fullPath } : {} })
  }
})
