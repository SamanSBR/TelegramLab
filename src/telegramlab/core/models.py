"""Small domain types shared by future research modules."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class MessageRecord:
    """Privacy-conscious normalized message metadata."""

    peer_id: int
    message_id: int
    date: datetime
    text: str | None = None
    has_media: bool = False
