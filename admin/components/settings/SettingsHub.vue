<template>
  <div class="max-w-5xl space-y-8">
    <!-- everything that is set up once, as plain cards: what it is and why one would open it -->
    <div>
      <h1 class="page-title">{{ t('nav.settings') }}</h1>
      <p class="page-sub">{{ t('hub.sub') }}</p>
    </div>

    <section v-for="group in hubGroups" :key="group.key">
      <h2 class="mb-3 text-sm font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
        {{ t(`hub.group.${group.key}`) }}
      </h2>
      <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <NuxtLink
          v-for="item in group.items"
          :key="item.to"
          :to="item.to"
          class="card card-hover flex items-start gap-3.5 p-4 min-h-[88px] focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          :class="item.alert ? 'border-rose-300 dark:border-rose-500/50' : ''"
        >
          <span
            class="grid place-items-center w-10 h-10 shrink-0 rounded-button"
            :class="item.alert
              ? 'bg-[#fce6ea] text-[#a61b34] dark:bg-rose-500/15 dark:text-rose-300'
              : 'bg-[#e8f2fc] text-primary-dark dark:bg-sky-500/15 dark:text-sky-300'"
          >
            <UiIcon :name="item.alert ? 'alert' : item.icon" />
          </span>
          <span class="min-w-0">
            <span class="block font-semibold text-slate-900 dark:text-white">{{ t(`nav.${item.key}`) }}</span>
            <span v-if="item.alert" class="block mt-0.5 text-sm font-medium text-[#a61b34] dark:text-rose-300">{{ t('hub.roles_problem') }}</span>
            <span v-else class="block mt-0.5 text-sm leading-snug text-slate-500 dark:text-slate-400">{{ t(`hub.${item.key}`) }}</span>
          </span>
        </NuxtLink>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
const { t } = useI18n()
const { hubGroups } = useNav()
usePage(() => [{ label: t('nav.settings') }])
</script>
