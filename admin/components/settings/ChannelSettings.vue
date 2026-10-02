<template>
  <!-- the channel's own settings and its overrides of the tenant settings -->
  <form class="space-y-5" @submit.prevent="save">
    <div class="grid gap-4 md:grid-cols-2">
      <div>
        <label class="label">{{ t('channels.title') }}</label>
        <input v-model.trim="title" class="input" required :readonly="!canEdit" />
      </div>
      <div class="flex items-end">
        <label class="flex items-center gap-2 text-sm h-9">
          <input v-model="disabled" type="checkbox" class="checkbox" :disabled="!canEdit" />
          {{ t('channels.disabled') }}
        </label>
      </div>
    </div>

    <div class="grid gap-4 md:grid-cols-3">
      <label class="flex items-center gap-2 text-sm">
        <input v-model="form.operator_mode" type="checkbox" class="checkbox" :disabled="!canEdit" />
        {{ t('channels.operator_mode') }}
      </label>
      <div>
        <label class="label">{{ t('channels.merge_pause') }}</label>
        <input v-model.number="form.merge_pause_s" type="number" min="0" max="600" class="input" :placeholder="t('scenarios.default')" :readonly="!canEdit" />
      </div>
      <div>
        <label class="label">{{ t('channels.merge_max_wait') }}</label>
        <input v-model.number="form.merge_max_wait_s" type="number" min="0" max="600" class="input" :placeholder="t('scenarios.default')" :readonly="!canEdit" />
      </div>
    </div>
    <p class="hint -mt-3">{{ t('channels.merge_hint') }}</p>

    <div class="rounded-card border border-slate-200 dark:border-slate-700 p-4 space-y-4">
      <div>
        <h3 class="font-semibold text-sm">{{ t('channels.overrides') }}</h3>
        <p class="hint">{{ t('channels.overrides_hint') }}</p>
      </div>
      <label class="flex items-center gap-2 text-sm">
        <input v-model="ownLanguages" type="checkbox" class="checkbox" :disabled="!canEdit" />
        {{ t('channels.own_languages') }}
      </label>
      <LanguageRules
        v-if="ownLanguages"
        v-model:languages="form.languages"
        v-model:reply-language="form.reply_language"
        :readonly="!canEdit"
      />
      <p v-else class="text-sm muted">{{ t('channels.tenant_languages', { languages: tenantLanguages.join(', ') }) }}</p>
      <div>
        <label class="label">{{ t('settings.bot_name') }}</label>
        <input v-model.trim="form.bot_name" class="input md:w-1/2" :placeholder="tenantSettings.bot_name" :readonly="!canEdit" />
      </div>
      <div>
        <label class="label">{{ t('settings.business_description') }}</label>
        <textarea v-model="form.business_description" rows="4" class="textarea" :placeholder="tenantSettings.business_description" :readonly="!canEdit" />
      </div>
    </div>

    <p v-if="saveError" class="alert-red">{{ saveError }}</p>
    <div v-if="canEdit" class="flex justify-end">
      <button type="submit" class="btn-primary" :disabled="busy">{{ t('common.save') }}</button>
    </div>
  </form>
</template>

<script setup lang="ts">
const props = defineProps<{ channel: any; tenantSettings: any; canEdit: boolean }>()
const emit = defineEmits<{ saved: [] }>()
const { t } = useI18n()
const api = useApi()
const toast = useToast()
const { errorText } = useErrorText()
const { path } = useTenant()

const title = ref('')
const disabled = ref(false)
const form = ref<any>({})
const ownLanguages = ref(false)
const busy = ref(false)
const saveError = ref<string | null>(null)
const tenantLanguages = computed(() => (props.tenantSettings?.languages || []) as string[])

watch(
  () => props.channel,
  (c) => {
    const s = c?.settings || {}
    title.value = c?.title || ''
    disabled.value = c?.status === 'disabled'
    ownLanguages.value = !!(s.languages && s.languages.length)
    form.value = {
      operator_mode: !!s.operator_mode,
      merge_pause_s: s.merge_pause_s ?? '',
      merge_max_wait_s: s.merge_max_wait_s ?? '',
      languages: s.languages?.length ? [...s.languages] : [...tenantLanguages.value],
      reply_language: { ...(s.reply_language || props.tenantSettings?.reply_language || {}) },
      bot_name: s.bot_name || '',
      business_description: s.business_description || '',
    }
    saveError.value = null
  },
  { immediate: true },
)

async function save() {
  busy.value = true
  saveError.value = null
  const f = form.value
  // empty values are dropped by the server: the tenant value is used
  const settings: Record<string, any> = {
    operator_mode: f.operator_mode,
    merge_pause_s: f.merge_pause_s === '' ? null : f.merge_pause_s,
    merge_max_wait_s: f.merge_max_wait_s === '' ? null : f.merge_max_wait_s,
    bot_name: f.bot_name || null,
    business_description: f.business_description || null,
  }
  if (ownLanguages.value) {
    settings.languages = f.languages
    settings.reply_language = f.reply_language
  }
  try {
    await api(path(`/channels/${props.channel.id}`), {
      method: 'PATCH',
      body: { title: title.value, disabled: disabled.value, settings },
    })
    toast.ok(t('common.saved'))
    emit('saved')
  } catch (e) {
    saveError.value = errorText(e)
  } finally {
    busy.value = false
  }
}
</script>
