"""What goes into the indexes: the text of an item, hashes, knowledge chunks, point ids.

Pure functions — the index jobs (search/index.py) and the tests use them.
"""

import hashlib
import json
import re
import uuid
from decimal import Decimal
from typing import Any

from app.db.models import CatalogItem, KnowledgeSource

# fixed namespace for uuid5 point ids of knowledge chunks
CHUNK_NAMESPACE = uuid.UUID("5b8a9c3e-2f61-4c1e-9a0d-7e4f1b2c3d40")

# TODO(review): chunk size for the knowledge base; check answers with make eval
CHUNK_CHARS = 1200
CHUNK_OVERLAP = 150
MAX_ATTRIBUTE_TEXT = 200


def _attr_text(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(_attr_text(v) for v in value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)[:MAX_ATTRIBUTE_TEXT]


def item_text(item: CatalogItem) -> str:
    """Title, category, description and characteristics: what the item is found by."""
    parts = [item.title.strip()]
    if item.category:
        parts.append(item.category.strip())
    if item.description and item.description.strip():
        parts.append(item.description.strip())
    for key, value in sorted((item.attributes or {}).items()):
        if value in (None, "", []):
            continue
        parts.append(f"{key}: {_attr_text(value)}")
    return "\n".join(parts)


def _hash(data: Any) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _money(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


def item_hash(item: CatalogItem) -> str:
    """Changes when anything the indexes keep changes (text, filters, the active flag)."""
    return _hash(
        {
            "text": item_text(item),
            "title": item.title,
            "category": item.category,
            "price": _money(item.price),
            "currency": item.currency,
            "url": item.url,
            "attributes": item.attributes or {},
            "language": item.language,
            "active": item.is_active,
        }
    )


def source_hash(source: KnowledgeSource) -> str:
    return _hash(
        {
            "title": source.title,
            "body": source.body,
            "url": source.url,
            "language": source.language,
            "active": source.is_active,
        }
    )


_PARAGRAPH_RE = re.compile(r"\n\s*\n+")
_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+")


def _pieces(text: str, limit: int) -> list[str]:
    """Paragraphs; a paragraph longer than the limit → sentences; a sentence longer than
    the limit → cut by characters."""
    out: list[str] = []
    for para in _PARAGRAPH_RE.split(text):
        para = " ".join(para.split())
        if not para:
            continue
        if len(para) <= limit:
            out.append(para)
            continue
        for sentence in _SENTENCE_RE.split(para):
            while len(sentence) > limit:
                out.append(sentence[:limit])
                sentence = sentence[limit:]
            if sentence:
                out.append(sentence)
    return out


def chunk_text(
    text: str, *, max_chars: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP
) -> list[str]:
    """Pieces glued up to `max_chars`; each next chunk repeats the end of the previous
    one (`overlap` characters) so an answer cut in the middle is still found."""
    chunks: list[str] = []
    current = ""
    for piece in _pieces(text, max_chars):
        if current and len(current) + 1 + len(piece) > max_chars:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            # start the tail at a word boundary
            space = tail.find(" ")
            tail = tail[space + 1 :] if 0 <= space < len(tail) - 1 else ""
            current = f"{tail} {piece}".strip() if tail else piece
            if len(current) > max_chars:
                current = piece
        else:
            current = f"{current} {piece}".strip() if current else piece
    if current:
        chunks.append(current)
    return chunks


def source_chunks(source: KnowledgeSource) -> list[str]:
    """The title goes into every chunk: a piece of text alone often misses the subject."""
    title = source.title.strip()
    return [f"{title}\n{chunk}" if title else chunk for chunk in chunk_text(source.body)]


def chunk_id(source_id: uuid.UUID, position: int, text: str) -> uuid.UUID:
    digest = hashlib.sha256(text.encode()).hexdigest()[:16]
    return uuid.uuid5(CHUNK_NAMESPACE, f"{source_id}:{position}:{digest}")


def model_slug(model: str) -> str:
    """openai/text-embedding-3-large → openai_text_embedding_3_large (collection names)."""
    return re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")[:80]
