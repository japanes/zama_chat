<template>
  <div class="max-w-[860px] mx-auto space-y-5">
    <!-- How to write a scenario file: the description the server keeps next to the ready
         scenarios (scenarios/README.md), shown as a page and given out as files. -->
    <header class="flex flex-wrap items-center justify-between gap-3">
      <NuxtLink :to="`${base}/scenarios/new/file`" class="link inline-flex items-center gap-1.5 text-sm">
        <UiIcon name="chevron-left" :size="16" /> {{ t('editor.file.title') }}
      </NuxtLink>
      <div v-if="doc" class="flex flex-wrap gap-2.5">
        <button type="button" class="btn-secondary btn-sm" @click="downloadText('scenario-format.md', doc.text, 'text/markdown')">
          {{ t('editor.file.format_download') }}
        </button>
        <button type="button" class="btn-secondary btn-sm" @click="downloadText('scenario-example.yaml', doc.example, 'text/yaml')">
          {{ t('editor.file.example') }}
        </button>
      </div>
    </header>

    <UiState :loading="loading" :error="error" :has-data="!!doc" :retry="load">
      <p v-if="locale !== 'ru'" class="text-sm muted">{{ t('editor.file.format_language') }}</p>
      <!-- the project's own text, never a visitor's: safe to show as HTML -->
      <article class="card card-body doc" lang="ru" v-html="html" />
    </UiState>
  </div>
</template>

<script setup lang="ts">
import { marked } from 'marked'
import { downloadText } from '~/utils/download'

const { t, locale } = useI18n()
const api = useApi()
const { base, path } = useTenant()
usePage(() => [
  { label: t('nav.scenarios'), to: `${base.value}/scenarios` },
  { label: t('editor.file.title'), to: `${base.value}/scenarios/new/file` },
  { label: t('editor.file.format_title') },
])

const { data: doc, loading, error, load } = useLoader(() => api<{ text: string; example: string }>(path('/scenario-format')))
const html = computed(() => (doc.value ? (marked.parse(doc.value.text, { async: false }) as string) : ''))
onMounted(load)
</script>
