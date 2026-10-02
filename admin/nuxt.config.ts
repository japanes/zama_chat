// Zama Chat admin: a single-page app (no SSR) in the style of the factory dashboard.
// The browser talks only to this app; `/api/**` is proxied to the backend
// (server/routes/api/[...].ts), so the session cookie belongs to the admin's domain.

// Every locale is split into the same files (one per area of the admin);
// `make i18n-check` makes sure all locales have the same keys.
const LOCALE_FILES = ['common', 'errors', 'activity', 'scenarios', 'settings', 'models', 'operator', 'search', 'receipts', 'decisions']

const locale = (code: string, name: string) => ({
  code,
  name,
  files: LOCALE_FILES.map((f) => `${code}/${f}.json`),
})

export default defineNuxtConfig({
  compatibilityDate: '2024-11-01',
  ssr: false,
  devServer: { port: 3120 },

  modules: ['@nuxtjs/tailwindcss', '@pinia/nuxt', '@nuxtjs/i18n', '@vueuse/nuxt'],
  css: ['~/assets/css/main.css'],
  // main.css already has the @tailwind directives; without this the module adds its own copy
  tailwindcss: { cssPath: false },
  components: [{ path: '~/components', pathPrefix: false }],

  runtimeConfig: {
    // NUXT_BACKEND_URL in production
    backendUrl: 'http://localhost:8090',
  },

  app: {
    head: {
      title: 'Zama Chat',
      meta: [{ name: 'theme-color', content: '#141B3B' }],
      link: [
        { rel: 'preconnect', href: 'https://fonts.googleapis.com' },
        { rel: 'preconnect', href: 'https://fonts.gstatic.com', crossorigin: '' },
        {
          rel: 'stylesheet',
          href: 'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&family=Plus+Jakarta+Sans:wght@600;700&display=swap',
        },
      ],
    },
  },

  i18n: {
    // the interface language is a setting of the user (users.locale), not a part of
    // the URL; the first visit takes it from the browser
    strategy: 'no_prefix',
    defaultLocale: 'en',
    lazy: true,
    langDir: 'locales',
    vueI18n: './i18n.config.ts',
    bundle: { optimizeTranslationDirective: false },
    locales: [locale('en', 'English'), locale('uk', 'Українська'), locale('ru', 'Русский')],
    detectBrowserLanguage: {
      useCookie: true,
      cookieKey: 'zama_locale',
      fallbackLocale: 'en',
      redirectOn: 'root',
    },
  },
})
