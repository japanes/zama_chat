// File ⇄ editor: a scenario opened in the editor and saved without a change must stay the
// same scenario (or saving would quietly drop its settings). Run: npm test (node --test).
import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import yaml from 'js-yaml'
import { cleanDefinition, newDefinition, newField, newShowIf, toForm } from '../utils/scenario.ts'

const root = fileURLToPath(new URL('../../scenarios/', import.meta.url))
const files = readdirSync(root).filter((name) => name.endsWith('.yaml'))
const roundTrip = (d) => cleanDefinition(toForm(d))

// every setting of the format the editor knows, with values that are not the defaults
const FULL = JSON.parse(readFileSync(new URL('./full-scenario.json', import.meta.url), 'utf8'))

for (const name of files) {
  test(`${name}: saving without a change gives the same scenario`, () => {
    const source = yaml.load(readFileSync(join(root, name), 'utf8'))
    const once = roundTrip(source)
    // the same as the file in the server's eyes: backend tests/unit/test_scenario_editor.py
    assert.deepEqual(roundTrip(once), once)
  })
}

test('every new setting comes back as it was written', () => {
  const once = roundTrip(FULL)
  assert.deepEqual(once.fields[1].show_if, FULL.fields[1].show_if)
  assert.deepEqual(once.fields[2].show_if, FULL.fields[2].show_if)
  assert.equal(once.fields[3].options_from, 'caller')
  assert.equal(once.fields[3].options, undefined)
  assert.deepEqual(once.waiting, FULL.waiting)
  assert.deepEqual(once.ask_rules, FULL.ask_rules)
  assert.equal(once.history_messages, 12)
  assert.deepEqual(once.added, FULL.added)
  assert.deepEqual(roundTrip(once), once)
})

test('defaults are left out: the server fills them in the same way', () => {
  const d = toForm(FULL)
  d.fields[1].show_if.on_unknown = 'ask'
  d.fields[1].show_if.recheck = 'every_turn'
  d.added = { wording: 'own_words', close_after: 3, max_fields: '' }
  d.ask_rules = [{ ru: ' ' }]
  d.history_messages = ''
  const out = cleanDefinition(d)
  assert.equal('on_unknown' in out.fields[1].show_if, false)
  assert.equal('recheck' in out.fields[1].show_if, false)
  assert.equal('added' in out, false)
  assert.equal('ask_rules' in out, false)
  assert.equal('history_messages' in out, false)
})

test('the by_caller settings go only with by_caller (the server refuses them otherwise)', () => {
  const d = toForm(FULL)
  d.completion = 'all_fields'
  const out = cleanDefinition(d)
  assert.equal('added' in out, false)
  assert.equal('waiting' in out, false)
})

test('options typed here are sent once the caller no longer sends them', () => {
  const d = toForm(FULL)
  d.fields[3].options_from = null
  d.fields[3].options = [
    { key: 'a', label: { ru: 'А' } },
    { key: 'b', label: { ru: 'Б' } },
  ]
  const out = cleanDefinition(d)
  assert.equal('options_from' in out.fields[3], false)
  assert.equal(out.fields[3].options.length, 2)
})

test('a new scenario and a new question carry none of the new settings', () => {
  const d = newDefinition('ru')
  d.key = 'x'
  d.fields = [newField(1)]
  const out = cleanDefinition(d)
  for (const key of ['ask_rules', 'history_messages', 'added', 'waiting']) assert.equal(key in out, false, key)
  for (const key of ['show_if', 'options_from']) assert.equal(key in out.fields[0], false, key)
})

test('a condition added in the form is a valid one: one question, enough by itself', () => {
  const d = toForm(FULL)
  d.fields[0].show_if = newShowIf()
  d.fields[0].show_if.questions[0].ask = { ru: 'Подходит?' }
  const out = cleanDefinition(d)
  assert.deepEqual(out.fields[0].show_if, { questions: [{ key: 'applies', ask: { ru: 'Подходит?' }, weight: 1 }], threshold: 1 })
})
