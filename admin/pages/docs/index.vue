<template>
  <div class="max-w-[860px] mx-auto space-y-6">
    <!-- The documentation: one address for every tenant, in the language of the interface. -->
    <header>
      <h1 class="page-title">{{ t('docs.title') }}</h1>
      <p class="page-sub">{{ t('docs.sub') }}</p>
    </header>
    <ul class="grid gap-3 sm:grid-cols-2">
      <li v-for="name in DOCS" :key="name">
        <NuxtLink
          :to="`/docs/${name}`"
          class="card card-body block h-full transition-colors hover:border-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        >
          <h2 class="text-[17px] font-semibold text-slate-900 dark:text-white">{{ t(`docs.items.${docKey(name)}.title`) }}</h2>
          <p class="mt-1 text-sm text-slate-600 dark:text-slate-300">{{ t(`docs.items.${docKey(name)}.sub`) }}</p>
        </NuxtLink>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { DOCS, docKey } from '~/utils/docs'

const { t } = useI18n()
usePage(() => [{ label: t('docs.title') }])
</script>
