// The editor's round trip (file → form → file) of scenario files, as JSON on stdout:
// [{file, source, saved}]. backend tests/unit/test_scenario_editor.py compares the two
// in the server's eyes. Usage: node tests/roundtrip.mjs <file.yaml|file.json>…
import { readFileSync } from 'node:fs'
import yaml from 'js-yaml'
import { cleanDefinition, toForm } from '../utils/scenario.ts'

const out = process.argv.slice(2).map((file) => {
  const text = readFileSync(file, 'utf8')
  const source = file.endsWith('.json') ? JSON.parse(text) : yaml.load(text)
  return { file, source, saved: cleanDefinition(toForm(source)) }
})
process.stdout.write(JSON.stringify(out))
