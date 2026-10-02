"""Prompts written for zama_chat (not legacy): seeded together with docs/legacy-prompts
and edited / versioned the same way in the admin.

- extract        — values of scenario fields from the client's message (LLM `extract`),
                   adapted from the legacy check_script_answers;
- scenario_step  — rules for the reply model when a scenario step is in the reply.
"""

import json
from typing import Any

EXTRACT_KEY = "extract"
SCENARIO_STEP_KEY = "scenario_step"

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
        "If 'current_value' is given, return only what the client says now (new or "
        "corrected words), not the old value",
    ],
    "fields": [],
    "client_message": "",
    "rewritten": "",
    "bot_question": "",
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
        "If 'ask_next.options' is given, list the options so the client can choose one",
        "If 'clarify' is given, the client's answer is not complete: say briefly what is "
        "still missing (see 'clarify.missing') and ask for it, without repeating the whole "
        "question",
        "If 'scenario_completed' is given, thank the client and say that everything needed "
        "is collected",
        "If 'scenario_cancelled' is given, confirm it calmly, without persuading",
        "If 'texts_language' is given, the scenario texts are in that language: translate "
        "them into the answer language",
        "Never ask more than one scenario question in one message",
    ]
}


def builtin_prompts() -> dict[str, str]:
    return {
        EXTRACT_KEY: json.dumps(EXTRACT, ensure_ascii=False, indent=4),
        SCENARIO_STEP_KEY: json.dumps(SCENARIO_STEP, ensure_ascii=False, indent=4),
    }
