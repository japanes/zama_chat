// A scenario definition handed from the «new scenario» page to the editor: a template
// or a draft built from a description. Taken once, so a reload starts from a blank.
const draft = ref<any | null>(null)

export function useScenarioDraft() {
  return {
    put(definition: any) {
      draft.value = definition
    },
    take(): any | null {
      const value = draft.value
      draft.value = null
      return value
    },
  }
}
