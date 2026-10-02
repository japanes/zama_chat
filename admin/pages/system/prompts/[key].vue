<template>
  <PromptEditor :prompt-key="key" :tenant-id="null" :can-write="auth.isSuper" />
</template>

<script setup lang="ts">
import { useAuthStore } from '~/stores/auth'

const { t } = useI18n()
const route = useRoute()
const auth = useAuthStore()
const key = computed(() => String(route.params.key))
usePage(() => [{ label: t('nav.shared_prompts'), to: '/system/prompts' }, { label: key.value }])
</script>
