<template>
  <div ref="root" class="relative">
    <button
      type="button"
      class="flex items-center gap-2 p-1.5 rounded-button hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
      :aria-expanded="open"
      aria-haspopup="true"
      @click="open = !open"
    >
      <span class="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center overflow-hidden">
        <img v-if="user?.avatar_url" :src="user.avatar_url" alt="" class="w-full h-full object-cover" referrerpolicy="no-referrer" />
        <span v-else class="text-primary font-medium text-sm">{{ initials }}</span>
      </span>
    </button>

    <Transition name="dropdown">
      <div
        v-if="open"
        class="absolute right-0 top-full mt-1 w-[240px] bg-white dark:bg-slate-800 rounded-card shadow-lg border border-slate-200 dark:border-slate-700 py-1 z-50"
      >
        <div class="px-4 py-3 border-b border-slate-200 dark:border-slate-700">
          <div class="text-sm font-medium text-slate-900 dark:text-slate-100 truncate">{{ user?.name || user?.email }}</div>
          <div class="text-xs text-slate-500 dark:text-slate-400 truncate">{{ user?.email }}</div>
          <span v-if="user?.is_superadmin" class="badge-violet mt-1.5">{{ t('roles.superadmin') }}</span>
        </div>
        <NuxtLink
          to="/profile"
          class="flex items-center gap-3 px-4 py-2 text-sm text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700"
          @click="open = false"
        >
          <UiIcon name="user" :size="16" />
          {{ t('nav.profile') }}
        </NuxtLink>
        <div class="my-1 border-t border-slate-200 dark:border-slate-700" />
        <button
          type="button"
          class="flex items-center gap-3 w-full px-4 py-2 text-sm text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700"
          @click="logout"
        >
          <UiIcon name="logout" :size="16" />
          {{ t('nav.logout') }}
        </button>
      </div>
    </Transition>
  </div>
</template>

<script setup lang="ts">
import { useAuthStore } from '~/stores/auth'

const { t } = useI18n()
const auth = useAuthStore()
const user = computed(() => auth.user)
const open = ref(false)
const root = ref<HTMLElement | null>(null)

const initials = computed(() => {
  const source = user.value?.name || user.value?.email || '?'
  return source
    .split(/[\s@.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p.charAt(0).toUpperCase())
    .join('')
})

function logout() {
  open.value = false
  auth.logout()
}

onClickOutside(root, () => (open.value = false))
</script>

<style scoped>
.dropdown-enter-active,
.dropdown-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}
.dropdown-enter-from,
.dropdown-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
