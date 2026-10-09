// Scenario texts come as written: a plain string or {language: text}.
// For the admin's own screens pick the reader's language, then any other.
export function pickText(value: unknown, ...languages: (string | null | undefined)[]): string {
  if (typeof value === 'string') return value
  if (!value || typeof value !== 'object') return ''
  const dict = value as Record<string, string>
  for (const l of languages) if (l && dict[l]?.trim()) return dict[l]
  return Object.values(dict).find((v) => typeof v === 'string' && v.trim()) || ''
}

// "Олена Коваль" → "О"; for the round avatar of a contact
export function initial(name: string | null | undefined): string {
  return (name || '?').trim().charAt(0).toUpperCase() || '?'
}

export function contactName(c: any): string {
  return c?.name || (c?.username ? `@${c.username}` : '') || c?.external_id || '—'
}
