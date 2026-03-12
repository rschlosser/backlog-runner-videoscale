from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

SESSION_TIMEOUT = 1800  # 30 minutes


@dataclass
class Session:
    user_id: str
    conversation_id: str = ""
    state: str = "idle"  # idle, awaiting_response, awaiting_approval, executing
    last_activity: float = field(default_factory=time.time)
    pending_plan: str = ""

    def is_expired(self) -> bool:
        return time.time() - self.last_activity > SESSION_TIMEOUT

    def touch(self):
        self.last_activity = time.time()


class SessionStore:
    def __init__(self, sessions_dir: Path):
        self.sessions_dir = sessions_dir
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, Session] = {}
        self._load_persisted()

    def _load_persisted(self):
        for path in self.sessions_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text())
                # Ensure user_id is str (migration from int keys)
                data["user_id"] = str(data["user_id"])
                session = Session(**data)
                if not session.is_expired():
                    self._sessions[session.user_id] = session
                else:
                    path.unlink(missing_ok=True)
            except Exception as e:
                logger.warning(f"Failed to load session {path}: {e}")

    def _persist(self, session: Session):
        path = self.sessions_dir / f"{session.user_id}.json"
        path.write_text(json.dumps(asdict(session)))

    def get(self, user_id: str) -> Session | None:
        session = self._sessions.get(str(user_id))
        if session and session.is_expired():
            self.remove(user_id)
            return None
        return session

    def create(self, user_id: str) -> Session:
        uid = str(user_id)
        session = Session(user_id=uid)
        self._sessions[uid] = session
        self._persist(session)
        return session

    def update(self, session: Session):
        session.touch()
        self._sessions[session.user_id] = session
        self._persist(session)

    def remove(self, user_id: str):
        uid = str(user_id)
        self._sessions.pop(uid, None)
        path = self.sessions_dir / f"{uid}.json"
        path.unlink(missing_ok=True)

    def has_active_session(self, user_id: str) -> bool:
        session = self.get(str(user_id))
        return session is not None and session.state != "idle"
