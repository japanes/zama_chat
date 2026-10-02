<template>
  <!-- what the vision model read from a receipt next to what the scenario expects -->
  <table class="w-full text-xs">
    <thead>
      <tr class="muted text-left">
        <th class="font-normal pr-2" />
        <th class="font-normal pr-2">{{ t('receipts.read') }}</th>
        <th class="font-normal">{{ t('receipts.expected') }}</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="row in rows" :key="row.key" class="align-top">
        <td class="muted pr-2 py-0.5 whitespace-nowrap">{{ t(`receipts.fact.${row.key}`) }}</td>
        <td class="pr-2 py-0.5 break-words">{{ row.got ?? '—' }}</td>
        <td class="py-0.5 break-words muted">{{ row.want ?? '' }}</td>
      </tr>
    </tbody>
  </table>
</template>

<script setup lang="ts">
const props = defineProps<{ extracted?: Record<string, any> | null; expected?: Record<string, any> | null }>()
const { t } = useI18n()

const rows = computed(() => {
  const x = props.extracted || {}
  const e = props.expected || {}
  const tolerance = Number(e.tolerance) ? ` ± ${e.tolerance}` : ''
  return [
    { key: 'amount', got: x.amount, want: e.amount != null ? `${e.amount}${tolerance}` : null },
    { key: 'currency', got: x.currency, want: e.currency },
    { key: 'date', got: x.date, want: e.max_age_days != null ? t('receipts.max_age', { n: e.max_age_days }) : null },
    { key: 'recipient', got: [x.recipient, x.recipient_account].filter(Boolean).join(', ') || null, want: (e.recipients || []).join(', ') || null },
    { key: 'reference', got: x.reference, want: null },
    { key: 'purpose', got: x.purpose, want: null },
    { key: 'status', got: x.status, want: null },
  ].filter((r) => r.got != null || r.want != null)
})
</script>
