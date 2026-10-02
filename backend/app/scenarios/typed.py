"""Typed scenario fields (stage 6): the client's answer checked and turned into a machine
value by code. The field keeps the client's words (`value`); the machine value goes to
`norm` (and to `normalized` in the result JSON):

  date    → "2026-10-05"   own rules (сьогодні / завтра / в п'ятницю / 5.10), then
                           dateparser (uk / ru / en, dates from the future), then the
                           extract model's `__norm` as the last resort;
  time    → "14:30"        regex, then the model's `__norm`;
  phone   → "+380501234567" phonenumbers (E.164), the tenant's `phone_region` for numbers
                           without a country code;
  email   → "anna@mail.com";
  number  → 3 / 2.5        digits in the words, then the model's `__norm`;
  list    → [{...}, ...]   the items the model returned, counted and checked.

A value that does not pass (a past date, a broken phone…) gets an `issue` code: the field
becomes partial with the failed check "@<issue>", the Jev filters are not asked, and the
bot re-asks with the text of the issue (clarify_text).

The extract model is not asked about a phone / e-mail when code finds exactly one in the
message (scan_contacts).
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import phonenumbers

from app.logging import get_logger
from app.scenarios.schema import FieldDef, FieldType

log = get_logger(__name__)

ISSUE_PREFIX = "@"
DEFAULT_REGION = "UA"


@dataclass(frozen=True)
class Typed:
    value: str  # the client's words as stored in the field (a list is rendered as text)
    norm: Any = None
    issue: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    source: str | None = None  # code / llm — who produced `norm`


def local_now(now: datetime, timezone: str) -> datetime:
    try:
        return now.astimezone(ZoneInfo(timezone))
    except Exception:  # a broken setting must not stop the turn
        log.warning("typed.timezone_invalid", timezone=timezone)
        return now


# --- date ---------------------------------------------------------------------------------

_WORD_RE = re.compile(r"[^\W\d_]+(?:['ʼ’][^\W\d_]+)?", re.UNICODE)

TODAY = {"сьогодні", "сегодня", "today"}
TOMORROW = {"завтра", "tomorrow"}
AFTER_TOMORROW = {"післязавтра", "послезавтра"}
NEXT_WEEK = ("наступн", "следующ", "next")
# stems of weekday names, Monday = 0
WEEKDAYS: tuple[tuple[str, ...], ...] = (
    ("понеділ", "понедельн", "monday"),
    ("вівтор", "вторник", "tuesday"),
    ("середа", "середу", "середи", "среда", "среду", "среды", "wednesday"),
    ("четвер", "thursday"),
    ("п'ятниц", "пятниц", "friday"),
    ("субот", "saturday"),
    ("неділ", "воскресен", "sunday"),
)
_DMY_RE = re.compile(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2}|\d{4}))?\b")


def _words(text: str) -> list[str]:
    return [w.lower().replace("ʼ", "'").replace("’", "'") for w in _WORD_RE.findall(text)]


def _weekday(word: str) -> int | None:
    for index, stems in enumerate(WEEKDAYS):
        if any(word.startswith(stem) for stem in stems):
            return index
    return None


def own_date(text: str, today: date) -> date | None:
    """Relative words and D.M(.Y) — the cases dateparser gets wrong or does not know."""
    words = _words(text)
    lowered = text.lower()
    if "day after tomorrow" in lowered or any(w in AFTER_TOMORROW for w in words):
        return today + timedelta(days=2)
    if any(w in TOMORROW for w in words):
        return today + timedelta(days=1)
    if any(w in TODAY for w in words):
        return today
    for w in words:
        wd = _weekday(w)
        if wd is None:
            continue
        ahead = (wd - today.weekday()) % 7 or 7
        if any(w2.startswith(NEXT_WEEK) for w2 in words) and today.weekday() < wd:
            ahead += 7  # «наступної п'ятниці» said on Wednesday: not the day after tomorrow
        return today + timedelta(days=ahead)
    m = _DMY_RE.search(text)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else today.year
        if year < 100:
            year += 2000
        try:
            found = date(year, month, day)
        except ValueError:
            return None
        if m.group(3) is None and found < today:
            # «5.01» in October means next January
            try:
                found = date(year + 1, month, day)
            except ValueError:
                return None
        return found
    return None


def parsed_date(text: str, now_local: datetime) -> date | None:
    try:
        import dateparser
    except ImportError:  # pragma: no cover - the dependency is in pyproject
        return None
    try:
        found = dateparser.parse(
            text,
            languages=["uk", "ru", "en"],
            settings={
                "PREFER_DATES_FROM": "future",
                "RELATIVE_BASE": now_local.replace(tzinfo=None),
                "DATE_ORDER": "DMY",
                "REQUIRE_PARTS": ["day", "month"],
            },
        )
    except Exception:
        log.warning("typed.dateparser_failed", exc_info=True)
        return None
    return found.date() if found is not None else None


def _iso_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def normalize_date(f: FieldDef, text: str, llm_norm: Any, now_local: datetime) -> Typed:
    today = now_local.date()
    found, source = own_date(text, today), "code"
    if found is None:
        found = parsed_date(text, now_local)
    if found is None:
        found, source = _iso_date(llm_norm), "llm"
    if found is None:
        return Typed(text, None, "date_invalid")
    settings = f.date
    allow_past = settings.allow_past if settings is not None else False
    if not allow_past and found < today:
        return Typed(text, found.isoformat(), "date_in_past", source=source)
    ahead = settings.max_days_ahead if settings is not None else None
    if ahead is not None and found > today + timedelta(days=ahead):
        return Typed(text, found.isoformat(), "date_too_far", {"max": ahead}, source=source)
    return Typed(text, found.isoformat(), source=source)


# --- time ---------------------------------------------------------------------------------

_HM_RE = re.compile(r"\b([01]?\d|2[0-3])\s*[:.]\s*([0-5]\d)\b")
_AMPM_RE = re.compile(r"\b(1[0-2]|0?[1-9])(?:\s*[:.]\s*([0-5]\d))?\s*(a\.?m\.?|p\.?m\.?)", re.I)
_HOUR_RE = re.compile(r"\b([01]?\d|2[0-3])\b")
# «о 3 дня», «в 7 вечора»: an hour after noon
AFTERNOON = ("дня", "вечора", "вечера", "вечір", "вечер", "evening", "afternoon")
_LLM_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def own_time(text: str) -> str | None:
    lowered = text.lower()
    m = _AMPM_RE.search(lowered)
    if m:
        hour, minute = int(m.group(1)) % 12, int(m.group(2) or 0)
        if m.group(3).startswith("p"):
            hour += 12
        return f"{hour:02d}:{minute:02d}"
    afternoon = any(w in AFTERNOON for w in _words(lowered))
    m = _HM_RE.search(lowered)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
    else:
        hours = _HOUR_RE.findall(lowered)
        if len(hours) != 1:
            return None  # «з 10 до 12», no number: leave it to the model
        hour, minute = int(hours[0]), 0
    if afternoon and hour < 12:
        hour += 12
    return f"{hour:02d}:{minute:02d}"


def normalize_time(text: str, llm_norm: Any) -> Typed:
    found = own_time(text)
    if found is not None:
        return Typed(text, found, source="code")
    if isinstance(llm_norm, str) and _LLM_TIME_RE.match(llm_norm.strip()):
        return Typed(text, llm_norm.strip(), source="llm")
    return Typed(text, None, "time_invalid")


# --- phone / email ----------------------------------------------------------------------------


def normalize_phone(text: str, region: str) -> Typed:
    try:
        number = phonenumbers.parse(text, region)
    except phonenumbers.NumberParseException:
        number = None
    if number is None or not phonenumbers.is_valid_number(number):
        found = find_phones(text, region)
        if len(found) != 1:
            return Typed(text, None, "phone_invalid")
        return Typed(text, found[0], source="code")
    return Typed(
        text,
        phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164),
        source="code",
    )


def find_phones(text: str, region: str) -> list[str]:
    out: list[str] = []
    try:
        matches = phonenumbers.PhoneNumberMatcher(
            text, region, leniency=phonenumbers.Leniency.VALID
        )
        for match in matches:
            e164 = phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164)
            if e164 not in out:
                out.append(e164)
    except Exception:
        log.warning("typed.phone_scan_failed", exc_info=True)
    return out


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")


def find_emails(text: str) -> list[str]:
    out: list[str] = []
    for m in EMAIL_RE.finditer(text):
        email = m.group(0).lower().rstrip(".")
        if email not in out:
            out.append(email)
    return out


def normalize_email(text: str) -> Typed:
    found = find_emails(text)
    if len(found) != 1:
        return Typed(text, None, "email_invalid")
    return Typed(text, found[0], source="code")


# --- number -------------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        m = _NUMBER_RE.search(value.replace(" ", "").replace(" ", ""))
        if m:
            return float(m.group(0).replace(",", "."))
    return None


def normalize_number(f: FieldDef, text: str, llm_norm: Any) -> Typed:
    found, source = _number(text), "code"
    if found is None:
        found, source = _number(llm_norm), "llm"
    if found is None:
        return Typed(text, None, "number_invalid")
    settings = f.number
    norm: int | float = int(found) if found.is_integer() else found
    if settings is not None:
        if settings.integer and not found.is_integer():
            return Typed(text, norm, "number_not_integer", source=source)
        if settings.min is not None and found < settings.min:
            return Typed(text, norm, "number_too_small", {"min": _plain(settings.min)}, source)
        if settings.max is not None and found > settings.max:
            return Typed(text, norm, "number_too_large", {"max": _plain(settings.max)}, source)
    return Typed(text, norm, source=source)


def _plain(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


# --- list ---------------------------------------------------------------------------------


def list_items(f: FieldDef, raw: Any) -> list[Any]:
    """The model's array, cleaned: strings stripped, objects limited to the item fields."""
    items: list[Any] = []
    keys = [i.key for i in f.items.item_fields] if f.items is not None else []
    for item in raw if isinstance(raw, list) else []:
        if keys:
            if not isinstance(item, dict):
                continue
            clean = {k: str(item[k]).strip() for k in keys if item.get(k) not in (None, "")}
            if clean:
                items.append(clean)
        elif item not in (None, "") and str(item).strip():
            items.append(str(item).strip())
    return items


def list_text(items: list[Any]) -> str:
    lines = []
    for n, item in enumerate(items, 1):
        text = " — ".join(item.values()) if isinstance(item, dict) else str(item)
        lines.append(f"{n}. {text}")
    return "\n".join(lines)


def normalize_list(f: FieldDef, raw: Any) -> Typed:
    items = list_items(f, raw)
    text = list_text(items)
    settings = f.items
    min_items = settings.min_items if settings is not None else 1
    max_items = settings.max_items if settings is not None else 10
    if len(items) < min_items:
        return Typed(text, items, "list_too_few", {"min": min_items}, "llm")
    if len(items) > max_items:
        return Typed(text, items, "list_too_many", {"max": max_items}, "llm")
    required = [i.key for i in settings.item_fields if i.required] if settings is not None else []
    for n, item in enumerate(items, 1):
        missing = [k for k in required if not (isinstance(item, dict) and item.get(k))]
        if missing:
            return Typed(text, items, "list_item_incomplete", {"n": n, "fields": missing}, "llm")
    return Typed(text, items, source="llm")


# --- the entry point --------------------------------------------------------------------------


def is_typed(f: FieldDef) -> bool:
    return f.type in (
        FieldType.DATE,
        FieldType.TIME,
        FieldType.PHONE,
        FieldType.EMAIL,
        FieldType.NUMBER,
        FieldType.LIST,
    )


def normalize(
    f: FieldDef,
    value: Any,
    llm_norm: Any = None,
    *,
    now: datetime,
    timezone: str = "UTC",
    region: str = DEFAULT_REGION,
) -> Typed:
    """`value` — what the extract model (or the contact scan) gave: a string, or the
    array of a list field."""
    if f.type == FieldType.LIST:
        return normalize_list(f, value)
    text = str(value).strip()
    if f.type == FieldType.DATE:
        return normalize_date(f, text, llm_norm, local_now(now, timezone))
    if f.type == FieldType.TIME:
        return normalize_time(text, llm_norm)
    if f.type == FieldType.PHONE:
        return normalize_phone(text, region or DEFAULT_REGION)
    if f.type == FieldType.EMAIL:
        return normalize_email(text)
    if f.type == FieldType.NUMBER:
        return normalize_number(f, text, llm_norm)
    return Typed(text)


# --- grounding: is the value in the client's message? -----------------------------------------


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def typed_grounded(f: FieldDef, value: str, source: str) -> bool | None:
    """Grounding by type; None = use the usual word check (extract.grounded)."""
    if f.type == FieldType.PHONE:
        digits = _digits(value)
        # the last 7 digits are enough: the code may be written another way (+38 / 0)
        return len(digits) >= 7 and digits[-7:] in _digits(source)
    if f.type == FieldType.EMAIL:
        return value.strip().lower() in source.lower()
    if f.type == FieldType.TIME:
        numbers = [int(n) for n in re.findall(r"\d+", value)]
        if not numbers:
            return None
        found = {int(n) for n in re.findall(r"\d+", source)}
        return all(n in found for n in numbers)
    return None


# --- code before the model: phones and e-mails in the message ---------------------------------


def scan_contacts(text: str, region: str) -> dict[FieldType, list[str]]:
    return {
        FieldType.PHONE: find_phones(text, region or DEFAULT_REGION),
        FieldType.EMAIL: find_emails(text),
    }


# --- what the bot says about an issue ---------------------------------------------------------

# TODO(review): wording; the reply model words it anew, this is the meaning
ISSUE_TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "phone_invalid": "the phone number does not look complete or valid: ask for the full "
        "number with the operator or country code",
        "email_invalid": "the e-mail address does not look valid: ask to check it",
        "date_invalid": "the date is not clear: ask for the exact day and month",
        "date_in_past": "this date has already passed: ask for a date from today on",
        "date_too_far": "the date is too far ahead (at most {max} days from today): ask for "
        "an earlier date",
        "time_invalid": "the time is not clear: ask for the exact time",
        "number_invalid": "a number is needed: ask for it",
        "number_not_integer": "a whole number is needed",
        "number_too_small": "the number must be at least {min}",
        "number_too_large": "the number must be at most {max}",
        "list_too_few": "at least {min} items are needed",
        "list_too_many": "at most {max} items are possible",
        "list_item_incomplete": "item {n} misses: {fields}",
        "attachment_kind": "this kind of file does not fit: ask for {kinds}",
        "receipt_unreadable": "the receipt can not be read: ask for a clear photo or "
        "screenshot of the whole receipt",
        "receipt_not_receipt": "the picture is not a payment receipt: ask for the receipt",
        "receipt_rejected": "the receipt was not accepted: ask for a correct receipt",
    },
    "uk": {
        "phone_invalid": "номер телефону неповний або неправильний: попросіть повний номер "
        "з кодом оператора або країни",
        "email_invalid": "адреса пошти схожа на неправильну: попросіть перевірити її",
        "date_invalid": "дата незрозуміла: попросіть точний день і місяць",
        "date_in_past": "ця дата вже минула: попросіть дату від сьогодні",
        "date_too_far": "дата надто далеко (не більше {max} днів від сьогодні): попросіть "
        "ближчу дату",
        "time_invalid": "час незрозумілий: попросіть точний час",
        "number_invalid": "потрібне число: попросіть його",
        "number_not_integer": "потрібне ціле число",
        "number_too_small": "число має бути не менше {min}",
        "number_too_large": "число має бути не більше {max}",
        "list_too_few": "потрібно щонайменше {min}",
        "list_too_many": "можна не більше {max}",
        "list_item_incomplete": "у пункті {n} бракує: {fields}",
        "attachment_kind": "такий файл не підходить: попросіть {kinds}",
        "receipt_unreadable": "квитанцію не вдається прочитати: попросіть чітке фото або "
        "скріншот усієї квитанції",
        "receipt_not_receipt": "на зображенні не квитанція про оплату: попросіть квитанцію",
        "receipt_rejected": "квитанцію не прийнято: попросіть правильну квитанцію",
    },
    "ru": {
        "phone_invalid": "номер телефона неполный или неправильный: попросите полный номер "
        "с кодом оператора или страны",
        "email_invalid": "адрес почты похож на неправильный: попросите проверить его",
        "date_invalid": "дата непонятна: попросите точный день и месяц",
        "date_in_past": "эта дата уже прошла: попросите дату начиная с сегодня",
        "date_too_far": "дата слишком далеко (не больше {max} дней от сегодня): попросите "
        "дату ближе",
        "time_invalid": "время непонятно: попросите точное время",
        "number_invalid": "нужно число: попросите его",
        "number_not_integer": "нужно целое число",
        "number_too_small": "число должно быть не меньше {min}",
        "number_too_large": "число должно быть не больше {max}",
        "list_too_few": "нужно не меньше {min}",
        "list_too_many": "можно не больше {max}",
        "list_item_incomplete": "в пункте {n} не хватает: {fields}",
        "attachment_kind": "такой файл не подходит: попросите {kinds}",
        "receipt_unreadable": "квитанцию не удаётся прочитать: попросите чёткое фото или "
        "скриншот всей квитанции",
        "receipt_not_receipt": "на изображении не квитанция об оплате: попросите квитанцию",
        "receipt_rejected": "квитанцию не приняли: попросите правильную квитанцию",
    },
}


class _Safe(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "?"


def issue_key(issue: str) -> str:
    return f"{ISSUE_PREFIX}{issue}"


def is_issue(failed: str) -> bool:
    return failed.startswith(ISSUE_PREFIX)


def clarify_text(issue: str, params: dict[str, Any] | None, language: str) -> str:
    texts = ISSUE_TEXTS.get(language) or ISSUE_TEXTS["en"]
    template = texts.get(issue) or ISSUE_TEXTS["en"].get(issue) or issue
    values = {
        k: ", ".join(map(str, v)) if isinstance(v, list) else v for k, v in (params or {}).items()
    }
    return template.format_map(_Safe(values))
