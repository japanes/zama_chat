declare module '#app' {
  interface NuxtApp {
    $api: <T = any>(path: string, opts?: Record<string, any>) => Promise<T>
  }
  interface PageMeta {
    // reachable without a session (login, email link)
    public?: boolean
  }
}

declare module 'vue' {
  interface ComponentCustomProperties {
    $api: <T = any>(path: string, opts?: Record<string, any>) => Promise<T>
  }
}

export {}
