"""
The conversation between a company and the platform operator.

One thread per company, not per person. A company is a single party here: the
owner raises something, a colleague adds to it, and the operator answers the
company rather than whoever happened to type. That is what makes the history
worth keeping — anyone on either side can pick the thread up and see all of it.

Attachments are part of the point. "The number says Not verified" is a
screenshot; "the agent sounds wrong" is a recording of it. Asking someone to
describe either in words loses the thing that would have answered the question.
"""
from __future__ import annotations

import time
import uuid
from enum import Enum

from pydantic import BaseModel, Field


class Author(str, Enum):
    """Which side wrote it. There are only ever two."""

    COMPANY = "company"
    OPERATOR = "operator"


class AttachmentKind(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    VOICE = "voice"
    FILE = "file"


# What the browser may send, and what each is shown as. Deliberately a list
# rather than a `type/*` match: accepting every `application/*` would mean
# accepting executables, and this file is handed back to someone's browser.
ALLOWED_TYPES: dict[str, AttachmentKind] = {
    "image/png": AttachmentKind.IMAGE,
    "image/jpeg": AttachmentKind.IMAGE,
    "image/webp": AttachmentKind.IMAGE,
    "image/gif": AttachmentKind.IMAGE,
    "image/heic": AttachmentKind.IMAGE,
    "video/mp4": AttachmentKind.VIDEO,
    "video/webm": AttachmentKind.VIDEO,
    "video/quicktime": AttachmentKind.VIDEO,
    "audio/webm": AttachmentKind.VOICE,
    "audio/ogg": AttachmentKind.VOICE,
    "audio/mpeg": AttachmentKind.VOICE,
    "audio/mp4": AttachmentKind.VOICE,
    "audio/wav": AttachmentKind.VOICE,
    "application/pdf": AttachmentKind.FILE,
    "text/plain": AttachmentKind.FILE,
    "text/csv": AttachmentKind.FILE,
}

# A still frame is a few hundred KB; a screen recording of a failing call is
# tens of megabytes. One limit for both would either refuse the video or let
# someone fill the bucket with photographs.
MAX_BYTES: dict[AttachmentKind, int] = {
    AttachmentKind.IMAGE: 10 * 1024 * 1024,
    AttachmentKind.VIDEO: 40 * 1024 * 1024,
    AttachmentKind.VOICE: 10 * 1024 * 1024,
    AttachmentKind.FILE: 10 * 1024 * 1024,
}

EXTENSIONS: dict[str, str] = {
    "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp",
    "image/gif": "gif", "image/heic": "heic",
    "video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov",
    "audio/webm": "weba", "audio/ogg": "ogg", "audio/mpeg": "mp3",
    "audio/mp4": "m4a", "audio/wav": "wav",
    "application/pdf": "pdf", "text/plain": "txt", "text/csv": "csv",
}

MAX_BODY_CHARS = 4000


def kind_of(mime: str) -> AttachmentKind | None:
    return ALLOWED_TYPES.get((mime or "").split(";", 1)[0].strip().lower())


class Attachment(BaseModel):
    """A file on a message. `reference` is a storage reference, never a URL.

    The URL is minted per reader, signed and short-lived, so a reference that
    leaks out of the database is not something anyone can fetch.
    """

    reference: str = ""
    mime: str = ""
    name: str = ""
    bytes: int = 0
    kind: AttachmentKind = AttachmentKind.FILE
    #: For a voice note, so the player can show its length before loading it.
    duration_seconds: float = 0.0

    def public(self) -> dict:
        return {
            "mime": self.mime,
            "name": self.name,
            "bytes": self.bytes,
            "kind": self.kind.value,
            "durationSeconds": round(self.duration_seconds, 1),
        }


class SupportMessage(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    tenant_id: str = ""
    author: Author = Author.COMPANY
    #: Who typed it, for the company's own side. The operator is just "support".
    author_name: str = ""
    body: str = ""
    attachment: Attachment | None = None
    created_at: float = Field(default_factory=time.time)
    #: When the *other* side read it. Drives the one-tick / two-tick state.
    read_at: float | None = None

    def public(self) -> dict:
        return {
            "id": self.id,
            "author": self.author.value,
            "authorName": self.author_name,
            "body": self.body,
            "attachment": self.attachment.public() if self.attachment else None,
            "createdAt": self.created_at,
            "readAt": self.read_at,
        }


class SupportThread(BaseModel):
    """The per-company summary, so neither side has to read to know.

    The unread counts are stored rather than counted. Counting means reading
    every message of every company to draw one list of companies, which is the
    kind of query that is fine with four customers and not with four hundred.
    """

    tenant_id: str = ""
    last_message_at: float = 0.0
    last_preview: str = ""
    last_author: Author | None = None
    unread_for_company: int = 0
    unread_for_operator: int = 0
    #: Set by the company to ask for the operator's attention explicitly.
    company_waiting: bool = False

    def public(self) -> dict:
        return {
            "tenantId": self.tenant_id,
            "lastMessageAt": self.last_message_at,
            "lastPreview": self.last_preview,
            "lastAuthor": self.last_author.value if self.last_author else None,
            "unreadForCompany": self.unread_for_company,
            "unreadForOperator": self.unread_for_operator,
        }
