"""Prompts written for zama_chat (not legacy): seeded together with docs/legacy-prompts
and edited / versioned the same way in the admin.

- extract        — values of scenario fields from the client's message (LLM `extract`),
                   adapted from the legacy check_script_answers;
- scenario_step  — rules for the reply model when a scenario step is in the reply;
- select_presented — rewrite rules for picking one of the options the bot showed last
                   ("let's take the second one"), adapted from the legacy
                   context_manager_select_service;
- search_context — rules for the reply model about the found catalog items / knowledge;
- receipt_vision — reading a payment receipt from an image (LLM `vision`, stage 6).
"""

import json
from typing import Any

EXTRACT_KEY = "extract"
SCENARIO_STEP_KEY = "scenario_step"
SELECT_PRESENTED_KEY = "select_presented"
SEARCH_CONTEXT_KEY = "search_context"
RECEIPT_VISION_KEY = "receipt_vision"

# TODO(review): wording; check on real dialogues with make eval
EXTRACT: dict[str, Any] = {
    "task": (
        "You extract the client's answers to the questions in 'fields' from the client's "
        "message. Return a JSON object with one property per field id"
    ),
    "rules": [
        "Use only the text in 'client_message'. 'rewritten' is a hint where to look, "
        "'bot_question' is what the bot asked before; never take answers from them",
        "Copy the client's own words. Do not paraphrase, translate, correct or add anything",
        "Extract only explicitly stated information. Do not invent or guess",
        "If the message has no answer for a field, return null for it",
        "For a field with 'options' return the key of the one option the client chose. "
        "If more than one option fits or it is not clear which one, return null",
        "For a 'yes_no' field return 'yes' or 'no'; null if the client did not say",
        "For a 'catalog' field return the key of the one option the client named or chose "
        "(by its title, its number in the list the bot showed, or its price). If the client "
        "named something that is not among the options, or several options fit, return null",
        "If 'current_value' is given, return only what the client says now (new or "
        "corrected words), not the old value",
        "For a field with 'norm' also return the property named in 'norm.property': the "
        "same answer in the format of 'norm.format' (a date counted from 'today', a time "
        "in 24 hours, a number with a dot). Return null there if you are not sure",
        "For 'phone' and 'email' fields copy the number or the address exactly as written",
        "For a 'list' field return an array with every item the client listed. If "
        "'current_value' is given, it is the list so far: return the whole new list (keep "
        "the old items unless the client removes or changes them). With 'item_fields' each "
        "item is an object with those keys (null for a part the client did not say)",
    ],
    "fields": [],
    "client_message": "",
    "rewritten": "",
    "bot_question": "",
    "today": "",
}

# TODO(review): wording; check on real dialogues with make eval
SCENARIO_STEP: dict[str, Any] = {
    "rules": [
        "The bot is collecting the client's answers for a scenario. 'scenario_step' says "
        "what to do in this reply; its rules take priority over the other rules",
        "If 'answer_client_question_first' is true, first answer the client's question from "
        "'user_question' by the usual rules, then go on with the scenario step in the same "
        "message",
        "If it is false, the client is answering the bot: do not treat 'user_question' as a "
        "question and never say you have no answer to it",
        "If 'accepted_answers' is not empty, you may acknowledge them in one short phrase; "
        "do not repeat them back in full",
        "If 'ask_next' is given, end the message with one question that asks for it. "
        "'ask_next.question' is the meaning: word it naturally and differently each time, "
        "do not copy it word for word",
        "If 'ask_next.options' is given, list the options so the client can choose one: "
        "one per line, with the number and the price when the option has them, without "
        "other list marks",
        "If 'ask_next.from_catalog' is true, the client chooses from the company's catalog: "
        "name services, products, categories and prices only from 'ask_next.options', "
        "'ask_next.categories' or 'context'. Never take them from 'client_description' or "
        "from your own knowledge, and do not add examples of your own",
        "If 'ask_next.categories' is given, there are too many items to list: name all "
        "these categories and ask which one the client is interested in",
        "If 'ask_next.more_options' is true, add that there are other options too and the "
        "client can name what they need",
        "If 'clarify' is given, the client's answer is not complete: say briefly what is "
        "still missing (see 'clarify.missing') and ask for it, without repeating the whole "
        "question",
        "If 'scenario_completed' is given, thank the client and say that everything needed "
        "is collected",
        "If 'scenario_cancelled' is given, confirm it calmly, without persuading",
        "If 'texts_language' is given, the scenario texts are in that language: translate "
        "them into the answer language",
        "If 'confirm' is given, show the collected answers from 'confirm.summary' as a short "
        "list and ask the client to confirm them ('confirm.question' is the meaning). If "
        "'confirm.rejected' is true, the client said something is wrong: ask what to change",
        "'waiting' lists the client's requests that are passed on and wait for a "
        "confirmation: 'for' = 'event' (the business confirms it) or 'receipt_review' (a "
        "person checks the payment receipt). For an item with 'new' true say it is passed "
        "on (or the receipt is received and being checked) and the client will be told. "
        "Mention the other items only when the client asks about them. Never say it is "
        "booked, paid or done",
        "If 'ask_next.attachment' is true, ask the client to send it as a photo or a file",
        "If 'ask_next.payment' is given, tell the amount to pay ('amount' and 'currency') "
        "before asking for the receipt",
        "Never ask more than one scenario question in one message",
    ]
}


# TODO(review): wording; check «давайте другий», «той за 500», «перший» with make eval
SELECT_PRESENTED: dict[str, Any] = {
    "rules": [
        "'last_presented_options' is the numbered list of options the bot showed the client "
        "last. If the selected line picks one of them, the rewritten question must name that "
        "option by its full 'title' instead of the reference",
        "A reference is an ordinal or a position ('the second one', 'давайте другий', "
        "'перший', 'last one'), a number from the list ('2'), a price, or a part of the title",
        "A part of the title counts only when exactly one option has it and every word of the "
        "client's phrase is in that title. If a word of the client's phrase (a body part, "
        "a material, a size…) is missing in the title, or several options fit, keep the "
        "client's words unchanged",
        "Never add an option the client did not pick. If the line does not refer to the list "
        "at all, ignore 'last_presented_options'",
    ]
}

# TODO(review): wording; together with the legacy context rules of user_prompt
SEARCH_CONTEXT: dict[str, Any] = {
    "rules": [
        "'context' has what was found for this question: catalog items (kind 'item', "
        "numbered with 'n') and knowledge base texts (kind 'knowledge'). It is already "
        "checked: every entry fits the question",
        "When you list items, keep their numbers 'n' so the client can answer with a number",
        "Give a price only as it is in the item, with its currency. Never invent prices, "
        "terms, links or characteristics that are not in 'context'",
        "If 'context' is empty, the answer was not found: follow 'i_dont_know_rules'",
    ]
}


# TODO(review): wording; try on real receipts (Monobank, PrivatBank, card terminals)
RECEIPT_VISION: dict[str, Any] = {
    "task": (
        "The image is sent by a client as a payment receipt. Read it and return the JSON "
        "object of the schema. Copy what is written; never guess"
    ),
    "rules": [
        "is_receipt: true only for a payment receipt / bank transfer confirmation / "
        "payment screenshot; false for anything else",
        "amount: the paid amount as a number with a dot, without the currency and the fee; "
        "null if it is not visible",
        "currency: ISO 4217 code (UAH for грн / ₴, EUR, USD…); null if not visible",
        "date: the payment date and time as YYYY-MM-DDTHH:MM (or YYYY-MM-DD); null if not visible",
        "recipient: the receiver's name as written; recipient_account: the receiver's IBAN "
        "or card number (masked digits as on the receipt)",
        "reference: the receipt / transaction number; purpose: the payment purpose",
        "status: 'success' when the payment is done, 'pending' when it is in progress, "
        "'failed' when it failed, null when not shown",
        "readable: false when the image is too blurry or cut to read the amount",
    ],
    # nothing about the expected amount on purpose: the model must read, not confirm
}


def builtin_prompts() -> dict[str, str]:
    return {
        EXTRACT_KEY: json.dumps(EXTRACT, ensure_ascii=False, indent=4),
        SCENARIO_STEP_KEY: json.dumps(SCENARIO_STEP, ensure_ascii=False, indent=4),
        SELECT_PRESENTED_KEY: json.dumps(SELECT_PRESENTED, ensure_ascii=False, indent=4),
        SEARCH_CONTEXT_KEY: json.dumps(SEARCH_CONTEXT, ensure_ascii=False, indent=4),
        RECEIPT_VISION_KEY: json.dumps(RECEIPT_VISION, ensure_ascii=False, indent=4),
    }


def prompt_rules(prompts: dict[str, Any], key: str, default: dict[str, Any]) -> list[str]:
    """`rules` of a JSON prompt from the store; the builtin text when it is missing or
    broken (a broken edit must not stop the bot)."""
    from app.errors import AppError
    from app.prompts.render import parse_json_prompt

    prompt = prompts.get(key)
    if prompt is not None:
        try:
            rules = parse_json_prompt(prompt.body, prompt.key).get("rules")
        except AppError:
            rules = None
        if isinstance(rules, list) and rules:
            return [str(r) for r in rules]
    return list(default["rules"])
