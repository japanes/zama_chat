import type { InjectionKey, Ref } from 'vue'

// What the parts of the scenario editor share: the language the texts are edited in
// (one switch for the whole page) and whether the keys for developers are shown.
export interface EditorContext {
  lang: Ref<string>
  showKeys: Ref<boolean>
}

export const EDITOR_CONTEXT: InjectionKey<EditorContext> = Symbol('scenario-editor')

export function useEditorContext(): EditorContext | null {
  return inject(EDITOR_CONTEXT, null)
}

// "ru" → "Русский" in the admin's language; the code itself when the browser has no name
export function languageName(code: string, locale: string): string {
  try {
    const name = new Intl.DisplayNames([locale], { type: 'language' }).of(code)
    if (name && name !== code) return name.charAt(0).toUpperCase() + name.slice(1)
  } catch {
    // an unknown code: show it as is
  }
  return code
}
