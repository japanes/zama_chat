// Short notices in the corner ("Saved", an error). Module-level for the same reason as
// useConfirm.
export interface Toast {
  id: number
  text: string
  tone: 'ok' | 'error' | 'info'
}

const toasts = ref<Toast[]>([])
let seq = 0

export function useToast() {
  function push(text: string, tone: Toast['tone'] = 'ok', ms = 3500) {
    const id = ++seq
    toasts.value.push({ id, text, tone })
    setTimeout(() => dismiss(id), tone === 'error' ? ms * 2 : ms)
  }
  function dismiss(id: number) {
    toasts.value = toasts.value.filter((x) => x.id !== id)
  }
  return {
    toasts,
    dismiss,
    ok: (text: string) => push(text, 'ok'),
    error: (text: string) => push(text, 'error'),
    info: (text: string) => push(text, 'info'),
  }
}
