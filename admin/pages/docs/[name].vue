<template>
  <div class="max-w-[860px] mx-auto space-y-5">
    <!-- A page of the documentation: its text in the language of the interface, shown as a
         page and given out as files. Changing the language changes the text. -->
    <header class="flex flex-wrap items-center justify-between gap-3">
      <NuxtLink to="/docs" class="link inline-flex items-center gap-1.5 text-sm">
        <UiIcon name="chevron-left" :size="16" /> {{ t('docs.title') }}
      </NuxtLink>
      <div v-if="doc" class="flex flex-wrap gap-2.5">
        <NuxtLink v-if="name === 'scenario-file' && base && can('admin')" :to="`${base}/scenarios/new/file`" class="btn-primary btn-sm">
          <UiIcon name="upload" :size="16" /> {{ t('editor.file.button') }}
        </NuxtLink>
        <button v-if="doc.example" type="button" class="btn-secondary btn-sm" @click="downloadText('scenario-example.yaml', doc.example, 'text/yaml')">
          {{ t('editor.file.example') }}
        </button>
        <button type="button" class="btn-secondary btn-sm" @click="downloadText(`${name}.md`, doc.text, 'text/markdown')">
          {{ t('docs.download') }}
        </button>
      </div>
    </header>

    <UiState :loading="loading" :error="error" :has-data="!!doc" :retry="load">
      <!-- the project's own text, never a visitor's: safe to show as HTML -->
      <article v-if="doc" class="card card-body doc" :lang="doc.language" v-html="html" />
      <p v-else class="alert-amber">{{ t('docs.missing') }}</p>
    </UiState>
  </div>
</template>

<script setup lang="ts">
import { marked } from 'marked'
import { DOCS, docKey, loadDoc } from '~/utils/docs'
import { downloadText } from '~/utils/download'

const { t, locale } = useI18n()
const route = useRoute()
const { base, can } = useTenant()

const name = computed(() => String(route.params.name))
const known = computed(() => DOCS.includes(name.value))
usePage(() => [
  { label: t('docs.title'), to: '/docs' },
  { label: known.value ? t(`docs.items.${docKey(name.value)}.title`) : name.value },
])

const { data: doc, loading, error, load } = useLoader(() => loadDoc(name.value, locale.value))
const html = computed(() => (doc.value ? (marked.parse(doc.value.text, { async: false }) as string) : ''))
// the text follows the language of the interface
watch([name, locale], load, { immediate: true })
</script>
