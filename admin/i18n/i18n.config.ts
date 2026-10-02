// Slavic plural rules for ru / uk: messages are written as "one | few | many"
// ("{n} хід | {n} ходи | {n} ходів"); English keeps "one | other".
function slavicPlural(choice: number, choicesLength: number): number {
  const n = Math.abs(Number(choice) || 0)
  const mod10 = n % 10
  const mod100 = n % 100
  let idx: number
  if (mod10 === 1 && mod100 !== 11) idx = 0
  else if (mod10 >= 2 && mod10 <= 4 && !(mod100 >= 12 && mod100 <= 14)) idx = 1
  else idx = 2
  return Math.min(idx, choicesLength - 1)
}

export default defineI18nConfig(() => ({
  fallbackLocale: 'en',
  pluralRules: { ru: slavicPlural, uk: slavicPlural },
}))
