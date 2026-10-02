<template>
  <div class="min-h-screen bg-slate-50 dark:bg-slate-900 flex">
    <!-- desktop sidebar -->
    <AppSidebar v-if="isDesktop" :collapsed="collapsed" @toggle="toggleCollapsed" />

    <div class="flex-1 min-w-0 flex flex-col">
      <AppHeader show-hamburger @menu="mobileOpen = true" />
      <div v-if="roleProblems > 0 && showRoleBanner" class="px-4 md:px-6 lg:px-8 pt-4">
        <div class="alert-red flex flex-wrap items-center gap-2">
          <UiIcon name="alert" :size="18" />
          <span class="flex-1">{{ t('models.roles.problem_banner') }}</span>
          <NuxtLink to="/models/roles" class="btn-sm btn-secondary">{{ t('models.roles.open') }}</NuxtLink>
        </div>
      </div>
      <main class="flex-1 min-w-0 p-4 md:p-6 lg:p-8">
        <slot />
      </main>
    </div>

    <!-- mobile sidebar -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="mobileOpen && !isDesktop" class="fixed inset-0 z-40 bg-slate-900/50" @click="mobileOpen = false" />
      </Transition>
      <Transition name="slide-left">
        <div v-if="mobileOpen && !isDesktop" class="fixed inset-y-0 left-0 z-50">
          <AppSidebar mobile @navigate="mobileOpen = false" />
        </div>
      </Transition>
    </Teleport>

    <ConfirmDialog />
    <ToastHost />
  </div>
</template>

<script setup lang="ts">
const { t } = useI18n()
const route = useRoute()
const isDesktop = useMediaQuery('(min-width: 1024px)')
const mobileOpen = ref(false)
const collapsed = ref(false)
const roleProblems = useRoleProblems()
const { tenant } = useTenant()

// the roles page shows its own banner
const showRoleBanner = computed(() => !route.path.startsWith('/models/roles'))

onMounted(() => {
  try {
    collapsed.value = localStorage.getItem('sidebar_collapsed') === '1'
  } catch {
    // storage blocked
  }
})

function toggleCollapsed() {
  collapsed.value = !collapsed.value
  try {
    localStorage.setItem('sidebar_collapsed', collapsed.value ? '1' : '0')
  } catch {
    // storage blocked
  }
}

watch(
  () => route.fullPath,
  () => (mobileOpen.value = false),
)

// remember the tenant of the page; check its roles once per tenant
watch(
  tenant,
  (value) => {
    if (route.params.tenant && value) rememberTenant(value.slug)
    refreshRoleProblems(value?.id || null)
  },
  { immediate: true },
)
</script>

<style scoped>
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
.slide-left-enter-active,
.slide-left-leave-active {
  transition: transform 0.2s ease;
}
.slide-left-enter-from,
.slide-left-leave-to {
  transform: translateX(-100%);
}
</style>
