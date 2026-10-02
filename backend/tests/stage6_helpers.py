"""Stage 6 scenario definitions: a salon booking that waits for the CRM, a cancellation
incompatible with it, a prepaid order with a receipt."""

from typing import Any

from tests.scenario_helpers import loaded, make_def


def _q(uk: str, en: str) -> dict[str, str]:
    return {"uk": uk, "en": en}


def salon_data(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "key": "salon",
        "title": _q("Запис", "Booking"),
        "language": "uk",
        "start": {"questions": [{"key": "wants_booking", "ask": _q("Записатися?", "Book?")}]},
        "fields": [
            {"key": "service", "question": _q("Яка послуга?", "Which service?")},
            {
                "key": "date",
                "type": "date",
                "question": _q("Яка дата?", "Which date?"),
                "date": {"max_days_ahead": 60},
            },
            {"key": "time", "type": "time", "question": _q("Котра година?", "What time?")},
            {"key": "phone", "type": "phone", "question": _q("Телефон?", "Phone?")},
        ],
        "confirm": {"question": _q("Усе вірно?", "All correct?")},
        "completion": "external_event",
        "events": {
            "booked": {
                "result": "completed",
                "say": _q("Записали на {date} о {time}.", "Booked for {date} at {time}."),
            },
            "slot_taken": {
                "result": "reopen",
                "reopen_fields": ["date", "time"],
                "say": _q("Час зайнятий.", "The time is taken."),
            },
            "failed": {"result": "cancelled"},
        },
        "waiting": {"hours": 2},
        "incompatible_with": ["cancel"],
        "resume": {"mode": "within_hours", "hours": 24},
        "actions": [
            {"on": "waiting", "do": "http_post", "endpoint": "results_api"},
            {"on": "completed", "do": "http_post", "endpoint": "results_api"},
        ],
    }
    data.update(over)
    return data


def cancel_data(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "key": "cancel",
        "title": _q("Скасування", "Cancel"),
        "language": "uk",
        "start": {"questions": [{"key": "wants_cancel", "ask": _q("Скасувати?", "Cancel?")}]},
        "fields": [{"key": "booking", "question": _q("Який запис?", "Which booking?")}],
        "completion": "explicit_yes",
        "actions": [
            {
                "on": "completed",
                "do": "http_post",
                "endpoint": "results_api",
                "requires_approval": True,
            }
        ],
    }
    data.update(over)
    return data


def order_data(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "key": "order",
        "title": _q("Замовлення", "Order"),
        "language": "uk",
        "start": {"questions": [{"key": "wants_order", "ask": _q("Замовити?", "Order?")}]},
        "fields": [
            {"key": "item", "question": _q("Що замовити?", "What to order?")},
            {"key": "phone", "type": "phone", "question": _q("Телефон?", "Phone?")},
            {
                "key": "payment",
                "type": "receipt",
                "question": _q("Надішліть квитанцію.", "Send the receipt."),
                "receipt": {
                    "amount": 500,
                    "currency": "UAH",
                    "recipients": ["ФОП Іваненко"],
                    "max_age_days": 3,
                },
            },
        ],
        "confirm": {},
        "completion": "receipt",
        "actions": [{"on": "completed", "do": "http_post", "endpoint": "results_api"}],
    }
    data.update(over)
    return data


def upload_data(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "key": "upload",
        "title": _q("Фото", "Photo"),
        "language": "uk",
        "start": {"questions": [{"key": "wants_upload", "ask": _q("Фото?", "Photo?")}]},
        "fields": [
            {"key": "name", "question": _q("Ім'я?", "Name?")},
            {
                "key": "photo",
                "type": "attachment",
                "question": _q("Надішліть фото.", "Send a photo."),
                "attachment": {"kinds": ["image"], "max_items": 2},
            },
        ],
    }
    data.update(over)
    return data


SALON = loaded(make_def(salon_data()), position=0)
CANCEL = loaded(make_def(cancel_data()), position=1)
ORDER = loaded(make_def(order_data()), position=2)
UPLOAD = loaded(make_def(upload_data()), position=3)
