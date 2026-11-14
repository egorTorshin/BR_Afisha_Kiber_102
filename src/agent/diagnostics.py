"""Privacy-minimized per-inference diagnostics for local model transports."""

from contextvars import ContextVar
from datetime import datetime, timezone
from time import perf_counter


_active = ContextVar("llm_inference_recorder", default=None)


class InferenceRecorder:
    def __init__(self, request_id: str, session_id: str, turn_ref: int):
        self.request_id = request_id
        self.session_id = session_id
        self.turn_ref = turn_ref
        self.rows: list[dict] = []

    @property
    def last_index(self) -> int | None:
        return len(self.rows) - 1 if self.rows else None

    def record(self, row: dict) -> int:
        row = {"request_id": self.request_id, "session_id": self.session_id,
               "turn_ref": self.turn_ref,
               "timestamp": datetime.now(timezone.utc).isoformat(), **row}
        self.rows.append(row)
        return len(self.rows) - 1

    def update(self, index: int | None, **values) -> None:
        if index is not None and 0 <= index < len(self.rows):
            self.rows[index].update(values)


def current() -> InferenceRecorder | None:
    return _active.get()


def activate(recorder: InferenceRecorder):
    return _active.set(recorder)


def deactivate(token) -> None:
    _active.reset(token)


def elapsed_ms(start: float) -> int:
    return max(0, round((perf_counter() - start) * 1000))


def update_current(index: int | None, **values) -> None:
    recorder = current()
    if recorder is not None:
        recorder.update(index, **values)


def latest_index() -> int | None:
    recorder = current()
    return recorder.last_index if recorder else None
