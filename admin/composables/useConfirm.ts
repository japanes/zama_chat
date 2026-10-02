// One shared confirmation dialog (rendered by <ConfirmDialog/> in the app layout).
//   if (!(await confirm({ message: t('…'), tone: 'danger' }))) return
// Module-level state: readable after an `await` where the Nuxt context is gone; safe
// because the admin is a SPA (one module scope per tab).
export interface ConfirmOptions {
  title?: string
  message?: string
  confirmLabel?: string
  cancelLabel?: string
  tone?: 'danger' | 'default'
  hideCancel?: boolean
}

const state = ref<{ open: boolean; opts: ConfirmOptions; resolve: ((v: boolean) => void) | null }>({
  open: false,
  opts: {},
  resolve: null,
})

export function useConfirm() {
  function confirm(opts: ConfirmOptions | string = {}): Promise<boolean> {
    if (state.value.open && state.value.resolve) state.value.resolve(false)
    const options = typeof opts === 'string' ? { message: opts } : opts
    return new Promise((resolve) => {
      state.value = { open: true, opts: options, resolve }
    })
  }
  function settle(result: boolean) {
    const r = state.value.resolve
    state.value = { open: false, opts: {}, resolve: null }
    r?.(result)
  }
  return { state, confirm, accept: () => settle(true), cancel: () => settle(false) }
}
