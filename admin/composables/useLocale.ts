// The interface language: the user's choice (users.locale), else the browser's.
// Switching saves it on the server, so it follows the user to another device.

export async function applyUserLocale(code: string) {
  const nuxtApp = useNuxtApp()
  const i18n = nuxtApp.$i18n as any
  const known = (i18n.locales.value as { code: string }[]).map((l) => l.code)
  const base = code.split('-')[0]
  if (known.includes(base) && i18n.locale.value !== base) await i18n.setLocale(base)
}

export function useLocaleSwitch() {
  const { locale, locales, setLocale } = useI18n()
  const api = useApi()
  async function switchTo(code: string) {
    await setLocale(code as any)
    try {
      await api('/auth/me', { method: 'PATCH', body: { locale: code }, noAuthRedirect: true })
    } catch {
      // not signed in (login page): the cookie keeps the choice
    }
  }
  return { locale, locales, switchTo }
}
