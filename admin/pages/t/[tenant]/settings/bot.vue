<template>
  <div class="space-y-5 max-w-3xl">
    <div>
      <h1 class="page-title">{{ t('nav.bot_settings') }}</h1>
      <p class="page-sub">{{ t('settings.sub') }}</p>
    </div>

    <UiState :loading="loading" :error="error" :has-data="!!form" :retry="load">
      <form v-if="form" class="space-y-5" @submit.prevent="save">
        <section class="card card-body space-y-4">
          <h2 class="card-title">{{ t('settings.section.company') }}</h2>
          <div class="grid gap-4 md:grid-cols-2">
            <div>
              <label class="label">{{ t('settings.tenant_title') }}</label>
              <input v-model.trim="title" class="input" required />
            </div>
            <div>
              <label class="label">{{ t('settings.company_title') }}</label>
              <input v-model.trim="form.company_title" class="input" />
            </div>
            <div>
              <label class="label">{{ t('settings.bot_name') }}</label>
              <input v-model.trim="form.bot_name" class="input" />
            </div>
            <div>
              <label class="label">{{ t('settings.timezone') }}</label>
              <input v-model.trim="form.timezone" class="input" list="timezones" />
              <datalist id="timezones">
                <option v-for="tz in timezones" :key="tz" :value="tz" />
              </datalist>
            </div>
          </div>
          <div>
            <label class="label">{{ t('settings.business_description') }}</label>
            <textarea v-model="form.business_description" rows="6" class="textarea" />
            <p class="hint">{{ t('settings.business_description_hint') }}</p>
          </div>
        </section>

        <section class="card card-body space-y-4">
          <h2 class="card-title">{{ t('settings.section.languages') }}</h2>
          <LanguageRules v-model:languages="form.languages" v-model:reply-language="form.reply_language" />
          <div class="md:w-1/2">
            <label class="label">{{ t('settings.jev_question_language') }}</label>
            <select v-model="form.jev_question_language" class="select">
              <option v-for="l in jevLanguages" :key="l" :value="l">{{ l }}</option>
            </select>
            <p class="hint">{{ t('settings.jev_question_language_hint') }}</p>
          </div>
        </section>

        <section class="card card-body space-y-4">
          <h2 class="card-title">{{ t('settings.section.search') }}</h2>
          <div class="md:w-1/2">
            <label class="label">{{ t('settings.unknown_answer') }}</label>
            <select v-model="form.unknown_answer" class="select">
              <option value="say">{{ t('settings.unknown.say') }}</option>
              <option value="handover">{{ t('settings.unknown.handover') }}</option>
            </select>
            <p class="hint">{{ t('settings.unknown_answer_hint') }}</p>
          </div>
        </section>

        <section class="card card-body space-y-4">
          <h2 class="card-title">{{ t('settings.section.timers') }}</h2>
          <div class="md:w-1/2">
            <label class="label">{{ t('settings.silence_minutes') }}</label>
            <input v-model.number="form.silence_minutes" type="number" min="1" class="input" />
            <p class="hint">{{ t('settings.silence_minutes_hint') }}</p>
          </div>
        </section>

        <section class="card card-body space-y-4">
          <h2 class="card-title">{{ t('settings.section.payments') }}</h2>
          <div class="grid gap-4 md:grid-cols-2">
            <div>
              <label class="label">{{ t('settings.phone_region') }}</label>
              <input v-model.trim="form.phone_region" class="input code uppercase" maxlength="2" placeholder="UA" />
              <p class="hint">{{ t('settings.phone_region_hint') }}</p>
            </div>
            <div>
              <label class="label">{{ t('settings.receipt_review') }}</label>
              <select v-model="form.receipt_review" class="select">
                <option value="always">{{ t('settings.receipt_review_mode.always') }}</option>
                <option value="issues_only">{{ t('settings.receipt_review_mode.issues_only') }}</option>
              </select>
              <p class="hint">{{ t('settings.receipt_review_hint') }}</p>
            </div>
          </div>
          <div v-if="form.receipt_review === 'issues_only'">
            <label class="label">{{ t('settings.receipt_above') }}</label>
            <p class="hint -mt-1 mb-2">{{ t('settings.receipt_above_hint') }}</p>
            <div v-for="(row, i) in thresholds" :key="i" class="flex items-center gap-2 mb-2">
              <input v-model.trim="row.currency" class="input w-24 code uppercase" maxlength="3" placeholder="UAH" />
              <input v-model="row.amount" type="number" min="0" step="0.01" class="input w-40" />
              <button type="button" class="btn-icon btn-ghost text-rose-600" :aria-label="t('common.delete')" @click="thresholds.splice(i, 1)">
                <UiIcon name="trash" :size="16" />
              </button>
            </div>
            <button type="button" class="btn-ghost btn-sm" @click="thresholds.push({ currency: '', amount: '' })">
              <UiIcon name="plus" :size="14" /> {{ t('settings.receipt_above_add') }}
            </button>
          </div>
        </section>

        <p v-if="saveError" class="alert-red">{{ saveError }}</p>
        <div class="flex justify-end">
          <button type="submit" class="btn-primary" :disabled="busy">{{ t('common.save') }}</button>
        </div>
      </form>
    </UiState>
  </div>
</template>

<script setup lang="ts">
import { useAuthStore } from '~/stores/auth'

const auth = useAuthStore()
const { t } = useI18n()
const api = useApi()
const toast = useToast()
const { errorText } = useErrorText()
const { path } = useTenant()
const { load: loadInfo } = useTenantInfo()
usePage(() => [{ label: t('nav.bot_settings') }])

const form = ref<any>(null)
const title = ref('')
const busy = ref(false)
const saveError = ref<string | null>(null)
// receipt_review_above {"UAH": "5000"} as rows the form can edit
const thresholds = ref<{ currency: string; amount: string | number }[]>([])

const timezones = (() => {
  try {
    return (Intl as any).supportedValuesOf('timeZone') as string[]
  } catch {
    return ['Europe/Kyiv', 'Europe/Warsaw', 'Europe/Berlin', 'Europe/London', 'UTC']
  }
})()
const jevLanguages = computed(() => {
  const out = ['en', ...(form.value?.languages || [])]
  return [...new Set(out)]
})

const { loading, error, load } = useLoader(async () => {
  const info = await loadInfo(true)
  title.value = info.tenant.title
  // the stored settings with defaults filled in; unknown keys stay as they are
  form.value = JSON.parse(JSON.stringify({ ...info.effective_settings, ...info.tenant.settings }))
  form.value.reply_language ||= {}
  thresholds.value = Object.entries(form.value.receipt_review_above || {}).map(([currency, amount]) => ({
    currency,
    amount: String(amount),
  }))
  return form.value
})

async function save() {
  busy.value = true
  saveError.value = null
  try {
    form.value.phone_region = (form.value.phone_region || '').toUpperCase()
    form.value.receipt_review_above = Object.fromEntries(
      thresholds.value
        .filter((r) => r.currency.trim() && r.amount !== '' && r.amount != null)
        .map((r) => [r.currency.trim().toUpperCase(), String(r.amount)]),
    )
    await api(path(), { method: 'PATCH', body: { title: title.value, settings: form.value } })
    await loadInfo(true)
    await auth.fetchMe() // the tenant title in the switcher
    toast.ok(t('common.saved'))
  } catch (e) {
    saveError.value = errorText(e)
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>
