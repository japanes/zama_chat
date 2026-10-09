<template>
  <div class="max-w-[980px] mx-auto space-y-7">
    <!-- Where a new scenario starts: describe it in words, take a ready one, or a blank.
         Each way ends in the editor with a draft that is not saved yet. -->
    <header class="space-y-1.5">
      <NuxtLink :to="`${base}/scenarios`" class="link inline-flex items-center gap-1.5 text-sm">
        <UiIcon name="chevron-left" :size="16" /> {{ t('editor.all') }}
      </NuxtLink>
      <h1 class="text-[28px] leading-tight font-bold text-slate-900 dark:text-white">{{ t('scenarios.new') }}</h1>
      <p class="page-sub max-w-[62ch]">{{ t('editor.list.sub') }}</p>
    </header>

    <form class="card card-body space-y-3" @submit.prevent="fromWords">
      <div>
        <label :for="wordsId" class="block text-[19px] font-semibold text-slate-900 dark:text-white">{{ t('editor.new.words_title') }}</label>
        <p class="mt-0.5 muted">{{ t('editor.new.words_sub') }}</p>
      </div>
      <textarea
        :id="wordsId"
        v-model="words"
        class="textarea min-h-[112px]"
        rows="4"
        :maxlength="WORDS_LIMIT"
        :placeholder="t('editor.new.words_placeholder')"
        :disabled="drafting"
        :aria-describedby="`${wordsId}-hint`"
      />
      <p v-if="wordsError" class="alert-red break-words" role="alert">{{ wordsError }}</p>
      <div class="flex flex-wrap items-center gap-x-4 gap-y-2">
        <button
          type="submit"
          class="inline-flex items-center justify-center min-h-[44px] px-5 rounded-button font-semibold bg-[#ec6ead] text-[#141b3b] hover:bg-[#f08bbd] transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-[#c94b8c] dark:focus-visible:ring-offset-slate-800 disabled:opacity-60 disabled:cursor-not-allowed"
          :disabled="drafting || !enough"
        >
          {{ t('editor.new.words_button') }}
        </button>
        <p v-if="drafting" class="text-sm text-slate-700 dark:text-slate-200" role="status">{{ t('editor.new.words_busy') }}</p>
        <p v-else :id="`${wordsId}-hint`" class="text-sm muted">{{ t('editor.new.words_hint') }}</p>
      </div>
    </form>

    <section class="space-y-3.5" :aria-labelledby="`${wordsId}-ready`">
      <h2 :id="`${wordsId}-ready`" class="text-[19px] font-semibold text-slate-900 dark:text-white">{{ t('editor.new.templates_title') }}</h2>
      <p v-if="loading" class="muted" role="status">{{ t('editor.new.templates_loading') }}</p>
      <p v-else-if="error" class="alert-amber flex flex-wrap items-center gap-x-3 gap-y-1" role="alert">
        <span>{{ t('editor.new.templates_error') }}</span>
        <button type="button" class="link" @click="load">{{ t('common.retry') }}</button>
      </p>

      <ul class="grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(min(300px,100%),1fr))]">
        <li v-for="tpl in templates" :key="tpl.key" class="card card-body flex flex-col gap-3">
          <div>
            <h3 class="text-[17px] font-semibold text-slate-900 dark:text-white">{{ text(tpl.title, tpl) || tpl.key }}</h3>
            <p v-if="text(tpl.description, tpl)" class="mt-1 text-sm text-slate-600 dark:text-slate-300">{{ text(tpl.description, tpl) }}</p>
          </div>
          <div>
            <p class="sr-only">{{ t('editor.new.asks') }}</p>
            <ul class="flex flex-wrap gap-1.5">
              <li v-for="name in asks(tpl).shown" :key="name" class="px-2.5 py-1 rounded-full bg-[#e6e9f2] dark:bg-slate-700 text-[13px] text-slate-800 dark:text-slate-200">{{ name }}</li>
              <li v-if="asks(tpl).more" class="px-1 py-1 text-[13px] muted">{{ t('editor.new.more', { n: asks(tpl).more }) }}</li>
            </ul>
          </div>
          <button type="button" class="btn-secondary w-full mt-auto" :disabled="drafting" @click="take(tpl)">{{ t('editor.new.take') }}</button>
        </li>
        <li class="card card-body flex flex-col gap-3 border-dashed">
          <div>
            <h3 class="text-[17px] font-semibold text-slate-900 dark:text-white">{{ t('editor.new.blank_title') }}</h3>
            <p class="mt-1 text-sm text-slate-600 dark:text-slate-300">{{ t('editor.new.blank_sub') }}</p>
          </div>
          <NuxtLink :to="`${base}/scenarios/new/edit`" class="btn-secondary w-full mt-auto">{{ t('editor.new.blank_button') }}</NuxtLink>
        </li>
      </ul>
    </section>
  </div>
</template>

<script setup lang="ts">
import { pickText } from '~/utils/texts'

// as much as the server takes; a couple of words are not a description yet
const WORDS_LIMIT = 4000
const WORDS_MIN = 10
// the names of the first questions tell what a template is; the rest is a number
const ASKS_SHOWN = 6

const { t, locale } = useI18n()
const api = useApi()
const toast = useToast()
const { errorText } = useErrorText()
const { base, path } = useTenant()
const { languages, load: loadInfo } = useTenantInfo()
const drafts = useScenarioDraft()
const wordsId = useId()
usePage(() => [{ label: t('nav.scenarios'), to: `${base.value}/scenarios` }, { label: t('scenarios.new') }])

const templates = ref<any[]>([])
const { loading, error, load } = useLoader(async () => {
  const [res] = await Promise.all([api<{ items: any[] }>(path('/scenario-templates')), loadInfo()])
  templates.value = res.items
  return res.items
})
onMounted(load)

// a template's texts in the reader's language, then in the template's own
function text(value: unknown, tpl: any): string {
  return pickText(value, locale.value, tpl.language)
}
function asks(tpl: any): { shown: string[]; more: number } {
  const names = (tpl.fields as any[]).map((f) => text(f.title, tpl) || f.key)
  // «1 more» takes the place of the name it hides
  const shown = names.length > ASKS_SHOWN + 1 ? names.slice(0, ASKS_SHOWN) : names
  return { shown, more: names.length - shown.length }
}

function open(definition: any) {
  drafts.put(definition)
  return navigateTo(`${base.value}/scenarios/new/edit`)
}

function take(tpl: any) {
  const definition = JSON.parse(JSON.stringify(tpl.definition))
  // a template is written in several languages: its main one becomes the bot's main one
  const main = languages.value[0]
  if (main && typeof definition.title === 'object' && definition.title?.[main]) definition.language = main
  return open(definition)
}

const words = ref('')
const drafting = ref(false)
const wordsError = ref('')
const enough = computed(() => words.value.trim().length >= WORDS_MIN)

async function fromWords() {
  if (drafting.value || !enough.value) return
  drafting.value = true
  wordsError.value = ''
  try {
    const res = await api<{ definition: any }>(path('/scenarios/draft'), { method: 'POST', body: { description: words.value.trim() } })
    toast.ok(t('editor.new.words_done'))
    await open(res.definition)
  } catch (e) {
    wordsError.value = errorText(e)
  } finally {
    drafting.value = false
  }
}
</script>
