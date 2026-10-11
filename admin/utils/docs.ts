// The admin's documentation: Markdown texts, one file per interface language
// (docs/<language>/<name>.md), shown at /docs/<name> in the language of the interface.
// A text is loaded when its page is opened, one language at a time.
const FILES = import.meta.glob<string>('~/docs/*/*.md', { query: '?raw', import: 'default' })

// the pages, in the order of the list at /docs
export const DOCS = ['scenario-file']
// the language a page is shown in when it has no text in the language of the interface
const FALLBACK = 'en'
// the example file of a page: the YAML block right after this mark
const EXAMPLE_RE = /<!-- example-file -->\s*```yaml\n([\s\S]*?\n)```/

export interface Doc {
  // the language the text is written in
  language: string
  // Markdown
  text: string
  // a complete file the page gives out («download an example»), '' when it has none
  example: string
}

export async function loadDoc(name: string, locale: string): Promise<Doc | null> {
  for (const language of [locale, FALLBACK]) {
    const path = Object.keys(FILES).find((file) => file.endsWith(`/docs/${language}/${name}.md`))
    if (!path) continue
    const text = await FILES[path]()
    return { language, text, example: EXAMPLE_RE.exec(text)?.[1] || '' }
  }
  return null
}

// «scenario-file» → the key of its texts: docs.items.scenario_file.*
export function docKey(name: string): string {
  return name.replace(/-/g, '_')
}
