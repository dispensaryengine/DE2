"""Structured event logger for the Dispensary Engine pipeline.

Writes to:
  - Rotating JSONL file (data/logs/engine_YYYYMMDD.jsonl) — always
  - Supabase pipeline_events table — async, non-blocking, best-effort
"""
import json
import logging
import time
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue, Empty

from config import DATA_DIR

LOGS_DIR = DATA_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# ── valid event types (must match Supabase CHECK constraint) ──────────────────
VALID_EVENTS = {
    "pipeline_start","pipeline_complete","pipeline_error",
    "scrape_start","scrape_complete","scrape_error","scrape_retry",
    "normalize_start","normalize_complete","brand_backfill",
    "mcp_created","mcp_updated","pek_collision","match_error",
    "snapshot_taken","delta_run","change_detected",
    "embed_start","embed_complete","embed_error","semantic_match",
    "schema_change","format_change","validation_error","data_anomaly",
    "pipeline_reset","config_change","migration_applied",
}

VALID_SEVERITY = {"debug","info","warning","error","critical"}


class EngineLogger:
    """Thread-safe structured logger with Supabase async drain."""

    def __init__(self, batch_id: str | None = None):
        self.batch_id = batch_id
        self._queue: Queue = Queue(maxsize=2000)
        self._drain_thread = threading.Thread(target=self._drain, daemon=True)
        self._drain_thread.start()
        self._sb = None  # lazy load

    def _sb_client(self):
        if self._sb is None:
            try:
                from engine.supabase_client import get_client
                self._sb = get_client()
            except Exception:
                self._sb = False  # disable if Supabase not available
        return self._sb if self._sb else None

    def _today_log(self) -> Path:
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        return LOGS_DIR / f"engine_{today}.jsonl"

    def _write_file(self, record: dict):
        try:
            with open(self._today_log(), "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception:
            pass

    def _drain(self):
        """Background thread: drain queue to Supabase in micro-batches."""
        buf = []
        while True:
            try:
                item = self._queue.get(timeout=2.0)
                buf.append(item)
                # batch up to 20 items
                while len(buf) < 20:
                    try:
                        buf.append(self._queue.get_nowait())
                    except Empty:
                        break
            except Empty:
                pass
            if buf:
                sb = self._sb_client()
                if sb:
                    try:
                        sb.table("pipeline_events").insert(buf).execute()
                    except Exception:
                        pass
                buf = []

    def emit(
        self,
        event_type: str,
        message: str,
        severity: str = "info",
        dispensary_id: str | None = None,
        mcp_id: str | None = None,
        source: str | None = None,
        duration_ms: int | None = None,
        payload: dict | None = None,
    ) -> str:
        """Emit a structured event. Returns event_id."""
        if event_type not in VALID_EVENTS:
            event_type = "data_anomaly"
        if severity not in VALID_SEVERITY:
            severity = "info"

        event_id = uuid.uuid4().hex
        record = {
            "event_id": event_id,
            "event_type": event_type,
            "batch_id": self.batch_id,
            "dispensary_id": dispensary_id,
            "mcp_id": mcp_id,
            "severity": severity,
            "source": source,
            "message": message,
            "payload": payload,
            "duration_ms": duration_ms,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._write_file(record)
        try:
            self._queue.put_nowait(record)
        except Exception:
            pass
        return event_id

    # ── convenience wrappers ──────────────────────────────────────────────────
    def info(self, event_type, message, **kwargs):
        return self.emit(event_type, message, severity="info", **kwargs)

    def warning(self, event_type, message, **kwargs):
        return self.emit(event_type, message, severity="warning", **kwargs)

    def error(self, event_type, message, **kwargs):
        return self.emit(event_type, message, severity="error", **kwargs)

    def scrape_start(self, dispensary_id: str, dispensary_name: str):
        return self.info("scrape_start", f"Scraping {dispensary_name}",
                         dispensary_id=dispensary_id,
                         source="pipeline.scrape_all")

    def scrape_done(self, dispensary_id: str, name: str, count: int, ms: int):
        return self.info("scrape_complete",
                         f"{name}: {count} listings in {ms}ms",
                         dispensary_id=dispensary_id,
                         duration_ms=ms,
                         payload={"product_count": count},
                         source="pipeline.scrape_all")

    def scrape_error(self, dispensary_id: str, name: str, exc: Exception):
        return self.error("scrape_error",
                          f"{name}: {type(exc).__name__}: {exc}",
                          dispensary_id=dispensary_id,
                          payload={"error": str(exc), "type": type(exc).__name__},
                          source="pipeline.scrape_all")

    def change_detected(self, dispensary_id: str, mcp_id: str,
                        change_type: str, detail: dict):
        return self.info("change_detected",
                         f"{change_type}: {detail.get('title','?')}",
                         dispensary_id=dispensary_id,
                         mcp_id=mcp_id,
                         payload={**detail, "change_type": change_type},
                         source="delta.detect_changes")

    def format_change(self, dispensary_id: str, field: str, old_fmt, new_fmt, example=None):
        return self.warning("format_change",
                            f"Format drift on {field}: {old_fmt!r} → {new_fmt!r}",
                            dispensary_id=dispensary_id,
                            payload={"field": field, "old": old_fmt, "new": new_fmt,
                                     "example": example},
                            source="pipeline.normalize_all")

    def flush(self, timeout: float = 5.0):
        """Block until the queue drains (or timeout)."""
        start = time.monotonic()
        while not self._queue.empty() and time.monotonic() - start < timeout:
            time.sleep(0.1)


# ── global logger (replaced per pipeline run) ─────────────────────────────────
_global: EngineLogger | None = None


def get_logger(batch_id: str | None = None) -> EngineLogger:
    global _global
    if _global is None or (batch_id and _global.batch_id != batch_id):
        _global = EngineLogger(batch_id=batch_id)
    return _global
