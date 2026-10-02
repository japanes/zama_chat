# Типы для классификации (context_manager_types), уникальные описания из старой БД

## ethical_violation

if the message refers to unethical, illegal, or dangerous topics (e.g., hacking, fraud, drugs, terrorism, incitement, political extremism, etc.)

---

## goodbye_only

if the message is a standalone farewell (e.g., "bye", "goodbye", "see you", "take care")

---

if the message is a standalone farewell (e.g., „Tschüss“, „Auf Wiedersehen“, „Bis bald“, „Mach’s gut“)

---

## greeting_only

if the message is a standalone greeting without any questions or requests (e.g., "hi", "hello", "good morning")

---

if the message is a standalone greeting without any questions or requests (e.g., „Hi“, „Hallo“, „Guten Morgen“, "hi", "hello", "good morning")

---

## not_a_bot

if the message asks about your identity, name, job, position, or whether you are a real person or a bot (e.g., "are you a bot?", "what’s your name?", "who are you?", or similar questions)

---

if the message asks about your identity, name, job, position, or whether you are a real person or a bot (e.g., „Bist du ein Bot?“, „Wie heißt du?“, „Wer bist du?“ or similar questions)

---

if the message asks about your identity, name, job, position, or whether you are a real person or a bot (наприклад, "ти бот?", "як тебе звати?", "хто ти?", або подібні запитання)

---

## order_issue

if the message reports a problem with an existing order — especially if it indicates that the order, package, or delivery has not arrived, is missing, delayed, or untraceable. This includes any messages where the user explicitly says they haven’t received their purchase, are still waiting, or want to know what happened to a recent order.

Trigger examples:
“I didn’t get my order”

“My package never arrived”

“Where is my shipment?”

“I placed an order 6 days ago and nothing came”

“Still waiting for delivery”

“My order is missing”

“There’s a problem with my order”

“I haven’t received anything yet”

“The tracking says delivered, but I got nothing”

Contextual interpretation rules:

Apply this type only if the message refers to an actual order already placed.

If the user only asks general questions about delivery timelines or shipping policies, use type: chat instead.

If the user wants to cancel, return, or modify an order, do not use this type

---

## product_filter

If the user's most recent message includes an intent to find, select, compare, replace, or pair products — even without descriptive attributes — and the request implies matching items from the available catalog, trigger the `product_filter` module immediately.

---

