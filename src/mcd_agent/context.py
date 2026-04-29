from __future__ import annotations

import json
import logging
from pathlib import Path

from .models import AgentSessionState

logger = logging.getLogger(__name__)


class SessionStore:
    def __init__(self, base_path: Path) -> None:
        self.base_path = base_path
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _session_path(self, session_id: str) -> Path:
        return self.base_path / f"{session_id}.json"

    def load(self, session_id: str) -> AgentSessionState:
        path = self._session_path(session_id)
        if not path.exists():
            logger.info("Creating new session state for session_id=%s", session_id)
            return AgentSessionState(session_id=session_id)

        data = json.loads(path.read_text(encoding="utf-8"))
        return AgentSessionState.model_validate(data)

    def save(self, state: AgentSessionState) -> None:
        path = self._session_path(state.session_id)
        path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        logger.info("Session state saved to %s", path)


class ContextManager:
    def __init__(self, max_history_messages: int = 12) -> None:
        self.max_history_messages = max_history_messages

    def append_user_message(self, state: AgentSessionState, content: str) -> None:
        state.history.append({"role": "user", "content": content})
        self._compact_history(state)

    def append_agent_message(self, state: AgentSessionState, content: str) -> None:
        state.history.append({"role": "assistant", "content": content})
        self._compact_history(state)

    def _compact_history(self, state: AgentSessionState) -> None:
        if len(state.history) <= self.max_history_messages:
            return

        overflow = state.history[:-self.max_history_messages]
        kept = state.history[-self.max_history_messages :]
        summary_lines = [
            f"{message['role']}: {message['content'][:180]}"
            for message in overflow
        ]
        if summary_lines:
            previous = f"{state.rolling_summary}\n" if state.rolling_summary else ""
            state.rolling_summary = (previous + "\n".join(summary_lines)).strip()
        state.history = kept

