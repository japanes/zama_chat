<template>
  <aside class="card overflow-hidden" :aria-label="t('editor.preview.title')">
    <!-- an example of the conversation, built from the scenario as it is now: the bot's
         side is the real texts, the client's side is a placeholder -->
    <div class="px-5 pt-4 pb-3 border-b border-slate-200 dark:border-slate-700">
      <p class="font-semibold text-slate-900 dark:text-white">{{ t('editor.preview.title') }}</p>
      <p class="text-sm muted">{{ t('editor.preview.sub') }}</p>
    </div>
    <div class="px-4 py-4 space-y-2.5 bg-slate-50 dark:bg-slate-900/40">
      <template v-for="(line, i) in lines" :key="i">
        <p v-if="line.kind === 'note'" class="text-center text-[13px] muted">{{ line.text }}</p>
        <div v-else-if="line.kind === 'client'" class="flex">
          <p class="max-w-[85%] rounded-[14px_14px_14px_4px] border border-dashed border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-3.5 py-2 text-sm italic muted">{{ line.text }}</p>
        </div>
        <div v-else class="flex flex-col items-end">
          <p class="max-w-[85%] rounded-[14px_14px_4px_14px] bg-primary-dark px-3.5 py-2 text-sm text-white whitespace-pre-line">{{ line.text }}</p>
          <p v-if="line.caption" class="mt-0.5 text-[12px] muted">{{ line.caption }}</p>
        </div>
      </template>
    </div>
    <div v-if="sandbox" class="px-5 py-3 border-t border-slate-200 dark:border-slate-700">
      <NuxtLink :to="sandbox" class="link">{{ t('editor.preview.try') }}</NuxtLink>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { fieldName } from '~/utils/scenario'
import { pickText } from '~/utils/texts'

interface Line {
  kind: 'bot' | 'client' | 'note'
  text: string
  caption?: string
}
const SHOWN = 3

const props = defineProps<{ def: any; sandbox?: string | null }>()
const { t } = useI18n()
const ctx = useEditorContext()

const lang = computed(() => ctx?.lang.value || props.def.language)

function text(value: unknown): string {
  return pickText(value, lang.value, props.def.language).trim()
}
function name(f: any): string {
  return fieldName(f, lang.value, props.def.language) || t('editor.q.untitled')
}
// "{service}" in a message → «Service»: what the client answered goes there
function filled(value: string): string {
  return value.replace(/\{([a-z0-9_.]+)\}/g, (whole, key: string) => {
    const f = (props.def.fields as any[]).find((x) => x.key === key)
    return f ? `«${name(f)}»` : whole
  })
}

const lines = computed<Line[]>(() => {
  const d = props.def
  const out: Line[] = []
  const fields = (d.fields as any[]).filter((f) => f.key)
  out.push({ kind: 'client', text: t('editor.preview.client_starts') })
  out.push({ kind: 'note', text: t('editor.preview.started') })
  for (const f of fields.slice(0, SHOWN)) {
    out.push({ kind: 'bot', text: text(f.question) || '…', caption: t('editor.preview.question', { name: name(f) }) })
    out.push({ kind: 'client', text: t('editor.preview.client_answers') })
  }
  if (fields.length > SHOWN) out.push({ kind: 'note', text: t('editor.preview.more', { n: fields.length - SHOWN }) })
  if (d.confirm || d.completion === 'explicit_yes') {
    out.push({
      kind: 'bot',
      text: `${t('editor.preview.summary')}\n${text(d.confirm?.question) || t('scenarios.confirm_default')}`,
      caption: t('editor.preview.confirm'),
    })
    out.push({ kind: 'client', text: t('editor.preview.client_yes') })
  }
  if (d.completion === 'external_event') {
    out.push({ kind: 'note', text: t('editor.preview.waiting') })
    const done = (d.events_list as any[]).find((e) => e.result === 'completed' && text(e.say))
    if (done) out.push({ kind: 'bot', text: filled(text(done.say)), caption: t('editor.preview.event', { name: done.name }) })
  } else if (d.completion === 'receipt') {
    out.push({ kind: 'note', text: t('editor.preview.receipt') })
  }
  for (const a of d.actions as any[]) {
    if (a.do === 'message_client' && a.on === 'completed' && text(a.params?.text)) {
      out.push({ kind: 'bot', text: filled(text(a.params.text)) })
    }
  }
  out.push({ kind: 'note', text: t('editor.preview.done') })
  return out
})
</script>
