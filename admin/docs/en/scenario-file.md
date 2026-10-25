# The scenario file: how to write and upload it

A **scenario** is a conversation in which the bot learns everything it needs from a client and hands you a finished request. A whole scenario is written in one text file. This document says what to write in the file and how to upload it.

Working examples of what is described below are the ready-made scenarios in the admin: «Scenarios» → «New scenario».

## 1. How to upload

1. In the admin open «Scenarios» → «Upload a file».
2. Choose a file (or several — one scenario in each).
3. The admin checks the file at once. If there is a mistake in it, the admin shows where exactly and saves nothing.
4. Press «Upload and switch on». The scenario starts working at once, nothing has to be restarted.

What happens on upload:

- **There is no scenario with this `key` yet** — it is created and switched on in the ticked channels (a channel is a place where the bot talks: the site, Instagram, the sandbox).
- **A scenario with this `key` already exists** — it gets a new version, and that version becomes the working one. The old versions stay in the history: any of them can be brought back in the scenario's editor, the «Version history» link.
- **The file is no different from the working version** — nothing changes: the admin says «No changes», there is nothing to upload.
- **Dialogues that already go by this scenario** reach their end by the version they started with. The new version is for conversations that start after the upload. To check the new version, start a new dialogue (for example, in the sandbox).

The file of a scenario that already works can be taken from its editor: at the bottom, «For developers» → «download as a file». Correct it and upload it back.

What a file must be: text in the UTF-8 encoding, the extension `.yaml`, `.yml` or `.json`, no more than 500,000 characters, one scenario per file.

Without the admin the same is done by a command on the server: `zama load-scenarios <file or folder> --tenant <tenant>`.

## 2. The smallest file

```yaml
key: callback_request
title: Callback request
language: en

start:
  questions:
    - key: wants_callback
      ask: Does the client ask to be called back?

fields:
  - key: name
    question: What should we call you?
  - key: phone
    type: phone
    question: What number should we call?
```

Everything here is required: the key, the title, the language, when to start (`start`) and what to ask (`fields`). The rest is added when it is needed.

## 3. Writing rules

The file is written in the YAML format: «name: value», nesting is shown by an indent of two spaces (not a tab), items of a list start with `- `. A line after `#` is a comment, the bot does not see it.

**Keys.** The values of `key` (of a scenario, a question, a check, an option) are names for the program: small Latin letters, digits and single underscores, starting with a letter, no longer than 64 characters. Fine: `main_offer`, `phone2`. Not fine: `MainOffer`, `main-offer`, `main__offer`, `2phone`, `оффер`. The client does not see keys.

**Texts.** Any text (a title, a question, a description, a check) is written in one of two ways:

```yaml
question: What should we call you?      # one line — the text in the scenario's main language

question:                               # or separately for each language
  en: What should we call you?
  uk: Як до вас звертатися?
```

If there is no text in the client's language, the bot takes the text in the main language (`language`) and translates it itself. On upload the admin only warns which translations are missing — this is not an error.

**Quotes.** A text has to be put in quotes if it has a colon followed by a space, if it starts with a sign (`"`, `'`, `[`, `{`, `*`, `&`, `!`, `%`, `@`, `|`, `>`, `-`, `?`), consists of digits only or looks like a time or a date (`10:00`, `2026-10-05`):

```yaml
title: "Interview: offer and positioning"
```

The words `yes`, `no`, `on`, `off` are plain text here. There are only two logical values: `true` and `false`.

**Anything extra is an error.** With a name that is not in this document (a typo, a made-up setting) the file does not pass the check. This way a typo does not go unnoticed.

**JSON.** The same scenario can be written in JSON — the names and the values are the same.

## 4. The top level of the file

| Name | Required | What it is |
|---|---|---|
| `key` | yes | The key of the scenario. By it the admin tells whether this is a new scenario or a new version of an old one. Do not change it for a working scenario: you would get a different scenario. |
| `title` | yes | The title — for you, in the admin and in notifications. |
| `language` | yes | The main language of the texts, a language code: `en`, `uk`, `ru`. |
| `description` | no | What the scenario is for, in one or two sentences. |
| `start` | yes | When the scenario starts — section 5. |
| `fields` | yes | The questions the bot asks — section 6. |
| `confirm` | no | Show the client what was collected and ask to confirm — section 7. |
| `completion` | no | When the scenario counts as finished — section 7. |
| `events`, `waiting` | no | The answer of your system (CRM) — section 8. |
| `resume` | no | What to do if the client disappeared and came back — section 9. |
| `reopen` | no | The client amends a request already collected — section 9. |
| `cancel` | no | How to tell that the client changed their mind — section 9. |
| `incompatible_with` | no | Scenarios that do not go at the same time as this one — section 9. |
| `only_scenario` | no | While the scenario is open, the bot does only it: no general replies, no search, no other scenarios — section 9. |
| `client_questions` | no | With `only_scenario`: the bot still answers the client's own question, then goes on with the scenario — section 9. |
| `background_answers` | no | The text answers are written after the bot's reply: the bot does not wait while a long story is read for every question — section 9. |
| `start_by_caller` | no | The scenario starts only by your system, never by the client's words — section 9. |
| `queue` | no | Its place in the queue of the conversation's scenarios — section 9. |
| `actions` | no | What to do with the result: send it to you, write to the client — section 10. |
| `ask_rules` | no | How the bot asks: rules for it alone — section 6. |
| `history_messages` | no | How many last messages of the chat the bot sees next to the answers — section 6. |
| `added` | no | Questions your system adds to a running conversation — section 8. |

## 5. `start` — when the scenario starts

Every message of the client is checked with the «yes / no» questions of `start`. Each question has a weight (`weight`). The scenario starts when the sum of the weights of the «yes» answers reaches the threshold (`threshold`).

```yaml
start:
  questions:
    - key: wants_offer_help            # a question of your own: a key + a text
      ask: Does the client want to sort out their offer or positioning?
      weight: 2
    - field: niche                     # a question about a field: «does the message answer the question niche?»
    - field: main_offer
  threshold: 2                         # start if the client asked directly (weight 2)
                                       # or named both the niche and the offer at once (1 + 1)
```

| Name | What it is |
|---|---|
| `questions` | A list of signs, at least one. A sign is either a question of your own (`key` + `ask`) or a reference to a question of the scenario (`field`: a key from `fields`). |
| `weight` | The weight of a sign, a whole number from 1 to 100. Not given — 1. |
| `threshold` | The threshold. Required when there is more than one sign. With one sign it may be left out. The threshold cannot be bigger than the sum of all weights — such a scenario would never start. |

A question in `ask` is written about the client in the third person, so that it can be answered «yes» or «no»: «Does the client want to book a service?».

## 6. `fields` — what the bot asks

Each item of the list is one question and one place for an answer.

```yaml
fields:
  - key: niche
    type: text
    title: Niche
    question: What do you do? What is your niche?
    description: The field and the exact niche of the client's business («manicure in my own studio», not just «beauty»).
    filters:
      - key: concrete_niche
        ask: Did the client name an exact niche, not only a general field?
  - key: positioning
    question: How are you different from your competitors?
    ask_after: [niche]
```

| Name | Required | What it is |
|---|---|---|
| `key` | yes | The key of the question. Under this name the answer goes into the finished request. It is not repeated within one scenario. |
| `type` | no | What kind of answer is expected — the table below. Not given — `text`. |
| `title` | no | A short name of the answer for the admin («Niche»). The client does not see it. |
| `question` | yes | What to ask. The bot words the question anew every time, in its own words, by the meaning of this text. |
| `description` | no | What counts as an answer. By this text the bot tells whether a message has an answer to this very question. |
| `filters` | no | Checks of the answer — see below. |
| `ask_after` | no | The keys of the questions after which this one is asked. Until they are answered, the bot does not ask this question. |
| `show_if` | no | When to ask the question: only if the client's answers say it applies — see below. |
| `options_from` | no | `choice` only: `caller` — your system sends the options at start — see below. |

**How the bot reads answers.** Every message of the client is checked against all the questions that have no answer yet, not only against the last one asked. The client can answer several questions in one message and in any order — the bot counts it and does not ask again. That is why similar questions must have different `description` texts: by them the bot tells which question the client's words belong to.

The bot asks the questions in the order they are written in the file, taking `ask_after` into account.

### Answer types

| `type` | What answer | What you get in the request | Its own settings |
|---|---|---|---|
| `text` | Any text | The client's words | — |
| `choice` | One of the options | The chosen option | `options` |
| `yes_no` | Yes or no | `yes` / `no` | — |
| `catalog` | A product or a service from your catalog | The name and the code of the item | `catalog` |
| `date` | A date («tomorrow», «on Friday», «5.10») | The client's words and the date `2026-10-05` | `date` |
| `time` | A time | The client's words and the time `14:30` | — |
| `phone` | A phone number | The number as `+380501234567` | — |
| `email` | An email | The address | — |
| `number` | A number | The number | `number` |
| `list` | Several items (guests, items of an order) | A list | `items` |
| `attachment` | A photo or a file from the client | The file | `attachment` |
| `receipt` | A payment receipt | The amount, the date and the number of the payment from the receipt | `receipt` |

A setting of a type can be written only for a question of that type: `options` for `text` is an error.

**`options`** — the options for `choice`, at least two:

```yaml
  - key: format
    type: choice
    question: Is online or the office more convenient for you?
    options:
      - key: online
        label: Online
      - key: office
        label: In the office
```

**`catalog`** — where to take the items for `catalog` from:

```yaml
    catalog:
      categories: [Haircuts, Colouring]    # only these sections of the catalog; empty — the whole catalog
      max_options: 5                        # the bot lists the items if there are no more than this (from 2 to 10)
```

**`date`**:

```yaml
    date:
      allow_past: false       # true — a date in the past is fine (a birthday); false — the bot asks again
      max_days_ahead: 60      # no further than this many days ahead; not given — no limit
```

**`number`**:

```yaml
    number:
      min: 1
      max: 50
      integer: true           # a whole number only
```

**`items`** — the settings for `list`:

```yaml
    items:
      min_items: 2            # from 1 to 50; not given — 1
      max_items: 6            # from 1 to 50; not given — 10
      item_fields:            # what each item consists of; without this an item is just a text
        - key: name
          label: Name
        - key: service
          label: Service
          required: false     # the item is taken without this part too; not given — true
```

**`attachment`**:

```yaml
    attachment:
      kinds: [image, file]    # what to take: image, video, audio, file
      max_items: 3            # how many files, from 1 to 10; not given — 1
```

**`receipt`** — what must be on the receipt:

```yaml
    receipt:
      amount: 50              # how much must be paid…
      currency: EUR           # …and in what currency (three capital letters); required with amount
      recipients: ["Lavender Studio", "DE89370400440532013000"]   # the recipient: a name, an account or the end of a card number
      max_age_days: 3         # the payment is no older than this many days (from 0 to 365)
      tolerance: 0            # how much the amount may differ
```

Instead of `amount` the amount can be taken from the catalog: `amount_field` — the key of a question of the `catalog` type (the price of the chosen item), `quantity_field` — the key of a question of the `number` type (multiply by the quantity). Without an amount the bot cannot check a receipt by itself, and every receipt goes to a person to check.

### `filters` — checks of the answer

A check is a «yes / no» question about the client's answer. The answer is accepted when all the checks of the question got a «yes». If not, the bot asks for details.

```yaml
    filters:
      - key: has_price
        ask: Did the client name a price or a price range?
```

| Name | Required | What it is |
|---|---|---|
| `key` | yes | The key of the check, not repeated within one question. |
| `ask` | yes | A question about the client's answer that can be answered «yes» or «no». «Yes» — the answer is fine. |
| `yes`, `no` | no | Your own words instead of «yes» and «no», if the check sounds more exact that way. Usually not needed. |

Questions of the `attachment` and `receipt` types cannot have checks: pictures are checked by the program, not by a question.

### `show_if` — when to ask the question

The bot asks a question with `show_if` only if the client's answers say it applies to the conversation. It works like `start`: yes/no signs with weights and a threshold. Only the signs are checked not against one message but against all the client's answers in this scenario.

```yaml
  - key: stakes
    question: What was at stake?
    ask_after: [story]
    show_if:
      questions:
        - key: risked
          ask: Did the client take a risk or could lose something, and what exactly was at stake is not said yet?
      on_unknown: ask          # the check gave no answer — ask (the default)
      recheck: every_turn      # check again after every answer (the default)
```

| Name | What it is |
|---|---|
| `questions` | The signs, at least one: `key` + `ask`, the weight `weight` (from 1 to 100, not given — 1). The question in `ask` is about the client's answers and can be answered “yes” or “no”. “Yes” — the question is asked. |
| `threshold` | The threshold, as in `start`. Required when there is more than one sign, and not bigger than the sum of the weights. |
| `on_unknown` | If the check gives no answer (too few answers yet, or the check did not answer): `ask` — ask, the default; `skip` — do not ask. |
| `recheck` | `every_turn` — check again after every answer that changed something: the question may show up later, when the client tells something new; the default. `once` — check once. |

A question that does not apply is not asked, but what the client said about it on their own is kept. In the finished request such questions go in the `hidden` list, not among the empty ones. A question with `show_if` cannot be named in `ask_after`: if it is never asked, the question waiting for it would never be asked either.

### `options_from` — your system sends the options

A `choice` may have no options in the file: your system sends them when it starts the scenario, each conversation its own. Then the file has `options_from: caller` and no `options`, and the request `POST /v1/runs/start` passes the options in `options`: the key of the question → from 1 to 10 options (`key` + `label`). Without them the scenario does not start.

```yaml
  - key: angle
    type: choice
    question: Which option do we take?
    options_from: caller
```

### `ask_rules` and `history_messages` — what the bot sees when it asks

When the bot writes a question, it always sees every answer of the client in this scenario — whole, with how it asked them — and the last messages of the chat. When the chat disagrees with the answers, the bot trusts the answers: the client may have corrected themselves (“I was 25… no, 27”).

```yaml
ask_rules:
  - Do not assume the event happened. Ask “Was anyone against it?”, not “Who was against it?”.
history_messages: 12
```

| Name | What it is |
|---|---|
| `ask_rules` | Rules for how to ask. Only the bot sees them, when it writes a question to the client: they do not affect reading and checking the answers. Not given — the bot's general rules. |
| `history_messages` | How many last messages of the chat the bot sees next to the answers, from 1 to 200. Not given — as for every scenario (a server setting, usually 30). |

## 7. Confirmation and completion

**`confirm`** — when all the answers are collected, the bot shows them to the client and asks to confirm:

```yaml
confirm:
  question: Please check. Is everything right?

confirm: {}       # the same with the standard question «Is everything right?»
```

**`completion`** — when the scenario counts as finished:

| Value | When it is finished |
|---|---|
| `all_fields` | All the questions have accepted answers (and the client confirmed, if there is `confirm`). This is the default. |
| `explicit_yes` | The same, and the client's confirmation is required even if `confirm` is not written. |
| `external_event` | The answers are collected, then the bot waits for the answer of your system — section 8. |
| `receipt` | The answers are collected and the receipt is accepted. At least one question of the `receipt` type is needed. |
| `by_caller` | The answers are collected, then the bot waits for new questions from your system until it closes the scenario — section 8. |

## 8. The answer of your system: `events` and `waiting`

Needed only with `completion: external_event`. The bot collected the request and waits for what your system (CRM) says: booked, the time is taken, refused. The system sends an event with the request `POST /v1/events`, and `events` says what to do with it.

```yaml
completion: external_event

events:
  booked:                     # the name of the event — your system sends it
    result: completed
    say: Done! You are booked for {service} on {date} at {time}.
  slot_taken:
    result: reopen
    reopen_fields: [date, time]
    say: This time is already taken. Please choose another day or time.
  failed:
    result: cancelled

waiting:
  hours: 24                   # wait no longer; then the scenario is closed as unfinished
```

| Name | What it is |
|---|---|
| `result` | `completed` — the scenario is finished; `cancelled` — cancelled; `reopen` — go back to the client and ask the questions from `reopen_fields` again. |
| `reopen_fields` | The keys of the questions to ask again. Required with `reopen`, not written in the other cases. |
| `say` | What the bot writes to the client. `{question_key}` is replaced with the client's answer, `{data.name}` — with a value from the data of the event. |
| `hours` | How many hours to wait for the event. Not given — wait with no time limit. |

### `by_caller` — your system sends new questions

With `completion: by_caller` the scenario does not end when the answers are collected: the bot says one short phrase and waits. Your system may add questions to the running conversation at any time — `POST /v1/runs/{run_id}/fields`, the bot asks them itself. When there will be no more questions, the system closes the scenario — `POST /v1/runs/{run_id}/finish`; the bot writes nothing to the client then.

```yaml
completion: by_caller

waiting:
  say: Thank you, that is enough material. I will put it all together now — it will take a little time.

added:
  wording: own_words          # the bot asks an added question in its own words
  close_after: 3              # after three failed answers the question is closed
  max_fields: 20              # at most 20 added questions
```

| Name | What it is |
|---|---|
| `say` | What to say when the questions run out. The bot says it in its own words. Not given — a short acknowledgement with no question. |
| `wording` | How the bot asks an added question: `own_words` — in its own words, from the client's answers, the default; `as_sent` — word for word, as sent. |
| `close_after` | After how many failed answers (they did not pass the checks) an added question is closed, from 1 to 20. What the client said is kept. Not given — 3. |
| `max_fields` | At most this many added questions per conversation, from 1 to 200. Not given — no limit. |

`added` and `waiting.say` are written only with `completion: by_caller`.

## 9. The client disappeared, changed their mind, started something else

**`resume`** — the client came back after a pause:

```yaml
resume:
  mode: within_hours          # always — continue at any time (a questionnaire, an interview), this is the default;
  hours: 24                   # within_hours — continue only within hours (a booking, an order):
                              # later the scenario is closed as unfinished, a new one starts from scratch
```

**`reopen`** — the client amends a request that is already collected. For example, after a collected reschedule they write «change the time too»: the bot takes that same request back to work (its answers are corrected from the new message, not asked again) and completes it again — the `completed` actions fire once more. No new request starts from scratch:

```yaml
reopen:
  hours: 48                   # how many hours after the completion the request can be amended, 0 to 720;
                              # not set — 24, 0 — never
```

Only with `completion: all_fields` or `explicit_yes`. If the client says they change something but names nothing new, the request stays closed as it was.

**`cancel`** — how to tell that the client changed their mind. Describe it in plain words — the bot compares every message with it and closes the scenario:

```yaml
cancel:
  description: The client says they do not need the booking or asks to stop the questions.
```

**`incompatible_with`** — the keys of the scenarios that cannot go at the same time as this one:

```yaml
incompatible_with: [cancel_booking]
```

**`only_scenario`** — while this scenario is open in the conversation (leading it, paused or waiting), the bot does nothing but scenarios:

```yaml
only_scenario: true           # off by default
```

- no separate reply to a greeting, a farewell or "who are you";
- no answer of its own to the client's question: whatever the client writes is an answer to the scenario's questions. A rhetorical question ("Do you see what is going on?") is not taken as a question to the bot, and the bot does not say "I can only help with the company's services";
- no hand-over to a person, no catalog or knowledge search;
- other scenarios do not start by themselves. A start by your system (`POST /v1/runs/start`) works as usual.

Everything about the scenario itself stays: answers, their checks, changing an answer, confirmation, "stop" and cancel. Use it when the whole conversation is given to one job — say, an interview where a person tells their story and the bot's general replies only get in the way.

**`client_questions`** — works together with `only_scenario`: the bot still notices the client's own question and answers it, then goes on with the scenario:

```yaml
only_scenario: true
client_questions: true        # off by default
```

Client: "Why do you need to know?" — the bot says in short why it asks and asks again. The bot does not search the catalog or the knowledge base for it: it answers from what it knows of the scenario. Without `client_questions` whatever the client writes is an answer to the scenario's questions.

**`background_answers`** — the text answers are written in the background, after the bot's reply:

```yaml
background_answers: true      # off by default
```

Sometimes a client answers several questions in one long message. Without this setting the bot first reads the whole text for every question and only then replies — with a long story that takes a while. With `background_answers`:

- the bot marks at once which questions the message answers, which it answers only in part, and which it does not answer;
- it asks the first unanswered question at once;
- the answers to the marked questions are written while the client replies to that question — one after another, with every check of `filters`. If the client later adds to an answer, the answer is extended, not replaced;
- a question the client answered in part is asked after its answer is written, and only for what is missing: «You say you work together with your wife. Who else is on the team?»;
- if the text turns out to hold no answer after all, the question is asked as usual.
- a correction of an earlier answer («not 5 years but 8») is written in the background too: the bot goes on at once, the old answer is replaced with the new one while the client replies, and whatever depends on it is checked again.

The bot waits for the answers only where it cannot go on without them: when there is nothing left to ask while answers are still being written, and before a question that depends on answers already given (`show_if`, `ask_after`).

Only `type: text` questions are written this way. The others — a date, a phone, a choice, a file — are read at once, as without this setting. Each background write is a turn of its own with the model: its cost shows in the conversation's trace. Your system sees this in the scenario's state (`POST /v1/runs/state`): `answers_pending` — the questions whose answers are still being written, `answer_turns` — the finished background writes. What each cost is given by `POST /v1/runs/{run_id}/usage` with `turns` — ids from `answer_turns`.

**`start_by_caller`** — the scenario starts only by your system (`POST /v1/runs/start`), never by the client's words: its `start` questions are not asked in any conversation.

```yaml
start_by_caller: true         # off by default
```

Use it for a scenario your interface opens itself — say, the parts of an interview your system starts in a conversation of its own. Without it a scenario switched on in the channel also starts in the channel's other conversations, when the client says something like its start questions.

**`queue`** — the place of the scenario in the queue of the conversation, from 1 to 100:

```yaml
queue: 2                      # not set — no place in the queue
```

Several scenarios can be open in one conversation: one leads it, the others are paused but listen — they take the answers to their questions from every message and ask nothing. When the leading one is finished, the dialogue goes to the paused scenario with the smallest place. Without places it goes to the paused one the client answered last. If the client asked to stop, nobody takes the dialogue by itself.

Your system manages such a conversation with three requests:

| Request | What it does |
|---|---|
| `POST /v1/runs/start` with `paused: true` | Opens the scenario paused: it listens, but asks nothing until it gets the dialogue. Without `paused` the start is refused while another scenario leads the conversation. |
| `POST /v1/runs/start` with `answers` | Answers your system already has, by the key of a text question. Each goes through the checks of its question, as an answer in the chat does. Passed — the question is not asked; not passed — the answer is kept and the bot asks the client to tell more. The bot then asks in its own words; the reply gives what the check cost (`check_usage`) and what the bot's turn cost (`usage`). |
| `POST /v1/runs/{run_id}/focus` | The dialogue goes to this scenario at once: the leading one is paused, this one asks its question in the bot's own words. What it already knows it does not ask again. |

Example: an interview in 8 parts. The system opens part 1, and parts 2–8 with `paused: true`, places 1 to 8. Answering part 1 the client tells something about part 4 — part 4 takes it. Part 1 is confirmed — part 2 leads. On another screen the client presses «Go through this part» at part 6 — `focus`, part 6 leads; when it is done, the dialogue goes back to part 2.

## 10. `actions` — what to do with the result

Every action is a «when» (`on`) and a «what to do» (`do`).

```yaml
actions:
  - on: completed
    do: http_post
    endpoint: results_api
  - on: silence
    after_minutes: 15
    do: http_post
    endpoint: results_api
    status: incomplete
  - on: completed
    do: message_client
    params:
      text: Thank you! A manager will contact you within an hour.
```

**When — `on`:**

| Value | When it fires |
|---|---|
| `started` | The scenario started. |
| `field_filled` | An answer to a question was accepted. |
| `field_stuck` | The bot asked again several times and still got no fitting answer. |
| `all_fields` | All the questions have answers. |
| `confirmed` | The client confirmed what was collected. |
| `waiting` | The request is collected, the bot waits for the answer of your system (`completion: external_event`) — the moment to send the request to the CRM. |
| `completed` | The scenario is finished. |
| `cancelled` | The client changed their mind. |
| `abandoned` | The scenario was closed as unfinished: the time of `resume` or `waiting` ran out. |
| `silence` | The client is silent. After how many minutes — in `after_minutes`. |

**What to do — `do`:**

| Value | What it does | What has to be given |
|---|---|---|
| `http_post` | Sends what was collected to your system. | `endpoint` — the name of an address from «Settings → Endpoints». |
| `notify_telegram` | Writes to your team in Telegram. | `params.notifier` — a name from «Settings → Notifications». |
| `notify_email` | Writes to your team by email. | `params.notifier` — a name from «Settings → Notifications». |
| `message_client` | The bot writes to the client. | `params.text` — the text. |
| `handover` | The bot goes silent, the dialogue passes to a person. | — |

**The other settings of an action:**

| Name | What it is |
|---|---|
| `endpoint` | The name of the delivery address for `http_post`. |
| `params` | The settings of the action itself: `notifier` or `text`. |
| `after_minutes` | Only for `silence`: how many minutes of silence. Not given — taken from the bot's settings. |
| `status` | The status the request goes out with. Usually not needed: the status is set by the event. An example: `incomplete` for a request the client did not finish. |
| `field` | Only for `field_filled` and `field_stuck`: the key of a question, so that the action fires for it alone. |
| `requires_approval` | `true` — the action waits until a person from the team presses «approve». |

A delivery address and a notification with these names must be set up in the admin, otherwise the action has nowhere to send to.

## 11. A complete example

This file can be uploaded as it is — it works. The «Download an example» button gives the same file.

<!-- example-file -->
```yaml
# A consultation request: the bot learns the name, the topic, the format, the day and
# the phone, shows the client what was collected and thanks them after the confirmation.
key: consultation_request
title:
  en: Consultation request
  uk: Заявка на консультацію
language: en
description: The bot takes a request for a consultation and passes it to you.

start:
  questions:
    - key: wants_consultation
      ask: Does the client want to book a consultation or ask to be consulted?
      weight: 2
    - field: topic
    - field: day
  threshold: 2            # asked for a consultation — or named both the topic and the day at once

fields:
  - key: name
    title: Name
    question:
      en: What should we call you?
      uk: Як до вас звертатися?
    description: The client's name — what to call them in the conversation.

  - key: topic
    title: Topic
    question: What question would you like to bring to the consultation?
    description: What the client wants to talk about at the consultation — their task or problem.
    filters:
      - key: concrete_topic
        ask: Did the client name an exact question or task, not only «I need a consultation»?

  - key: format
    type: choice
    title: Format
    question: Is online or the office more convenient for you?
    options:
      - key: online
        label: Online
      - key: office
        label: In the office

  - key: day
    type: date
    title: Day
    question: What day is convenient for you?
    date:
      max_days_ahead: 60

  - key: phone
    type: phone
    title: Phone
    question: Please leave your phone number — we will confirm the time.
    ask_after: [name]

confirm:
  question: Please check the request. Is everything right?

completion: all_fields

resume:
  mode: within_hours
  hours: 48

cancel:
  description: The client says they do not need a consultation or asks not to be asked questions.

actions:
  - on: completed
    do: message_client
    params:
      text: Thank you! We have received your request and will contact you soon.
  # To send the request to your system, set up an address in «Settings → Endpoints»
  # and remove the hash signs:
  # - on: completed
  #   do: http_post
  #   endpoint: results_api
```

## 12. What is checked on upload

**Errors** — the file is not saved, the admin says what is wrong:

- the file breaks the writing rules (an indent slipped, a quote is not closed) — the line number is shown;
- something required is missing (`key`, `title`, `language`, `start`, `fields`, for a question — `key` and `question`);
- an unknown name or a typo in a name;
- a key is written against the rules or is repeated;
- a reference to a question that does not exist (in `ask_after`, `start`, `reopen_fields`, the `field` of an action);
- questions wait for each other in a circle through `ask_after`;
- `choice` has fewer than two options; a setting of a type stands with a different type;
- a `choice` with `options_from: caller` has `options`, or `options_from` stands with another type;
- `ask_after` names a question with `show_if`; the keys of the signs in `show_if` repeat;
- the `threshold` is not given with several signs or is bigger than the sum of the weights;
- `completion: external_event` without `events`, or `events` / `waiting` with another `completion`;
- `added` or `waiting.say` with a `completion` other than `by_caller`;
- `resume` with `within_hours` without `hours`;
- an action lacks what it needs: `endpoint`, `params.notifier` or `params.text`.

**Warnings** — the file is saved, but it is worth a look:

- a text has no translation into one of the bot's languages (the bot translates it itself);
- the main language of the scenario is not among the bot's languages;
- a receipt has nothing to compare the amount with — every receipt goes to a person to check.

## 13. How to write questions and checks

- **`question`** — the way a person would ask. The bot does not repeat the text word for word, it asks the question by its meaning.
- **`description`** — what exactly counts as an answer and how it differs from the answers to the neighbouring questions. The more similar questions a scenario has («who are your clients» and «who are you not a fit for»), the more the descriptions matter: without them the client's words may go to the wrong question.
- **`filters`** — about the quality of the answer, not about the topic. «Did the client name an exact niche, not a general field?» is good. One check — one requirement.
- **The questions of `start`** — about the client's intention, not about the words in the message: «Does the client want to book a service?», not «Is there the word “booking”?».
- **Do not split** one answer into several questions and do not glue different answers into one question: one question — one thing you need to learn.
