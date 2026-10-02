// Page heading data for the header breadcrumbs and the browser tab title.
//   usePage(() => [{ label: t('nav.results'), to: `${base}/results` }, { label: run.id }])
export interface Crumb {
  label: string
  to?: string
}

const crumbs = ref<Crumb[]>([])

export function useCrumbs() {
  return crumbs
}

export function usePage(source: () => Crumb[]) {
  const current = computed(source)
  watch(current, (value) => (crumbs.value = value), { immediate: true })
  useHead({
    title: computed(() => {
      const last = current.value[current.value.length - 1]
      return last ? `${last.label} · Zama Chat` : 'Zama Chat'
    }),
  })
}
