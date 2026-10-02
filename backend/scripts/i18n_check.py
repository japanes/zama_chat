"""`make i18n-check`: translations stay complete.

1. Every admin locale (`admin/i18n/locales/<lang>/*.json`) has the same set of keys.
2. Every error / warning code the backend can return has a text in `errors.json` of
   every locale (the backend sends codes with params, the admin builds the text).
3. Every email language (`backend/app/admin/mail/*.json`) has the same keys.

Exit code 1 with a list of what is missing. Codes are found in the source: the first
string argument of AppError / NotFound / Forbidden / Unauthorized / Conflict, the value of
a `"code": "…"` pair, the code given to `_login_error` and the last argument of
`tenant_row`.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_APP = ROOT / "backend" / "app"
LOCALES = ROOT / "admin" / "i18n" / "locales"
MAIL = BACKEND_APP / "admin" / "mail"

CODE = r'"([a-z_]+\.[a-z_]+(?:\.[a-z_]+)*)"'
PATTERNS = [
    re.compile(r"\b(?:\w*Error|NotFound|Forbidden|Unauthorized|Conflict)\(\s*" + CODE, re.S),
    re.compile(r'"code":\s*' + CODE),
    re.compile(r"_login_error\(\s*\w+,\s*" + CODE),
    re.compile(r"\bproblem = " + CODE),
    # error subclasses with a fixed code: super().__init__("decisions.unavailable", …)
    re.compile(r"super\(\)\.__init__\(\s*" + CODE),
    # tenant_row(session, Model, id, tenant_id, "result.not_found")
    re.compile(r"\btenant_row\((?:[^()]|\([^()]*\))*?" + CODE + r"\s*,?\s*\)", re.S),
]


def flatten(data: dict, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            keys |= flatten(value, path)
        else:
            keys.add(path)
    return keys


def backend_codes() -> set[str]:
    codes: set[str] = set()
    for path in BACKEND_APP.rglob("*.py"):
        text = path.read_text("utf-8")
        for pattern in PATTERNS:
            codes.update(pattern.findall(text))
    return codes


def load_locale(lang_dir: Path) -> dict[str, set[str]]:
    """{file name: keys}; a file `errors.json` holds {"errors": {"auth": {...}}}."""
    out: dict[str, set[str]] = {}
    for f in sorted(lang_dir.glob("*.json")):
        out[f.name] = flatten(json.loads(f.read_text("utf-8")))
    return out


def main() -> int:
    problems: list[str] = []

    langs = sorted(p for p in LOCALES.iterdir() if p.is_dir()) if LOCALES.is_dir() else []
    if not langs:
        problems.append(f"no admin locales in {LOCALES}")
    loaded = {d.name: load_locale(d) for d in langs}
    all_keys: dict[str, set[str]] = {
        lang: set().union(*files.values()) if files else set() for lang, files in loaded.items()
    }
    union = set().union(*all_keys.values()) if all_keys else set()
    for lang, keys in all_keys.items():
        for key in sorted(union - keys):
            problems.append(f"admin/{lang}: missing {key}")
    files = {name for f in loaded.values() for name in f}
    for lang, f in loaded.items():
        for name in sorted(files - set(f)):
            problems.append(f"admin/{lang}: missing file {name}")

    for code in sorted(backend_codes()):
        for lang, keys in all_keys.items():
            if f"errors.{code}" not in keys:
                problems.append(f"admin/{lang}: no text for backend code {code}")

    mails = {p.stem: flatten(json.loads(p.read_text("utf-8"))) for p in MAIL.glob("*.json")}
    mail_union = set().union(*mails.values()) if mails else set()
    for lang, keys in sorted(mails.items()):
        for key in sorted(mail_union - keys):
            problems.append(f"mail/{lang}: missing {key}")

    if problems:
        print("\n".join(problems))
        print(f"\n{len(problems)} problem(s)")
        return 1
    print(f"ok: {len(all_keys)} admin locales, {len(backend_codes())} codes, {len(mails)} mails")
    return 0


if __name__ == "__main__":
    sys.exit(main())
