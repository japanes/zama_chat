"""How many tokens Jev counts for a text, next to our estimate (decisions/limits.py).

    python -m scripts.jev_tokens ru=path/ru.txt uk=path/uk.txt en=path/en.txt

Each text goes twice: as the state with one tiny question, and as the instructions of a
question with a one-character state; plus a base request (a state of one character, one
tiny question) — Jev's own part of a request. Prints characters (Cyrillic, ASCII), the
input tokens Jev counted, our estimate and the cost. A text of 8 000 characters a
language costs about a tenth of a cent for the whole run. The API key is only sent in the
header, never printed.
"""

import argparse
import asyncio
from decimal import Decimal
from pathlib import Path

import httpx

from app.db.models import DecisionType
from app.decisions.engine import Question
from app.decisions.jev_decider import JevDecider
from app.decisions.limits import _CYRILLIC, estimate_tokens, request_tokens
from app.settings import get_settings


def _question(text: str) -> Question:
    return Question("q", DecisionType.BOOLEAN, text, {"true": "Yes.", "false": "No."})


TINY = _question("Is it about apples?")


async def main(texts: dict[str, str]) -> None:
    cases = [("base", "x", [TINY])]
    for name, text in texts.items():
        cases.append((f"state:{name}", text, [TINY]))
        cases.append((f"question:{name}", "x", [_question(text)]))
    total = Decimal(0)
    async with httpx.AsyncClient() as http:
        jev = JevDecider(http, get_settings(), retry_delay_s=0)
        for label, state, questions in cases:
            call = await jev.evaluate(state, questions)
            usage = call.data.get("usage") or {}
            gateway = (call.data.get("providerMetadata") or {}).get("gateway") or {}
            cost = Decimal(str(gateway.get("cost") or 0))
            total += cost
            body = state if label.startswith("state") else questions[0].instructions
            cyrillic = len(_CYRILLIC.findall(body))
            ascii_ = len(body.encode("ascii", "ignore"))
            print(
                f"{label:16} status={call.status} chars={len(body)} cyrillic={cyrillic} "
                f"ascii={ascii_} jev={usage.get('inputTokens')} "
                f"estimate={request_tokens(state, questions)} (text {estimate_tokens(body)}) "
                f"cost={cost}"
            )
    print(f"total cost={total}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("texts", nargs="+", metavar="NAME=PATH")
    args = parser.parse_args()
    texts = {}
    for item in args.texts:
        name, _, path = item.partition("=")
        texts[name] = Path(path).read_text(encoding="utf-8")
    asyncio.run(main(texts))
