import json
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent


def record_event(
    session: Session,
    *,
    batch_id: int | None,
    actor: str,
    event_type: str,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        batch_id=batch_id,
        actor=actor,
        event_type=event_type,
        details_json=json.dumps(details or {}, sort_keys=True, separators=(",", ":")),
    )
    session.add(event)
    session.flush()
    return event
