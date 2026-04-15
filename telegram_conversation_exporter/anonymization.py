from __future__ import annotations

import hashlib
from collections import Counter

from .models import Participant, RawTelegramMessage


def build_participant_map(messages: list[RawTelegramMessage]) -> dict[str, Participant]:
    participant_messages = [
        message
        for message in messages
        if message.sender_key and message.sender_type in {"participant", "bot"}
    ]
    sender_keys = sorted({message.sender_key for message in participant_messages})
    counts = Counter(message.sender_key for message in participant_messages)
    mapping: dict[str, Participant] = {}
    for index, sender_key in enumerate(sender_keys, start=1):
        digest = hashlib.sha1(sender_key.encode("utf-8")).hexdigest()[:12]
        mapping[sender_key] = Participant(
            participant_id=f"participant_{digest}",
            label=f"Participant {index}",
            index=index,
            message_count=counts.get(sender_key, 0),
        )
    return mapping
