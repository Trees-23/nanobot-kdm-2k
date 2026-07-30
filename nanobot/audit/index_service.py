"""Gateway-owned background refresh service for the disposable audit index."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nanobot.audit.index import AuditIndexer, IndexRebuildRequired
from nanobot.audit.read_service import AuditReadService, IndexStatus, SanitizedIndexError


class AuditIndexService:
    def __init__(
        self,
        root: Path,
        *,
        enabled: bool = True,
        audit_mode: str = "full",
        refresh_interval: float = 1.5,
        active_run_ids: Callable[[], set[str]] | None = None,
        logger: Any | None = None,
    ) -> None:
        self.root = root
        self.indexer = AuditIndexer(root)
        self.refresh_interval = refresh_interval
        self._enabled = enabled
        self._audit_mode = audit_mode
        self._logger = logger
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        state = "disabled" if not enabled else "unavailable" if audit_mode == "off" else "building"
        self._status = IndexStatus(state=state)
        self.read_service = AuditReadService(
            self.indexer.index_path,
            status_provider=self.snapshot_status,
            active_run_ids=active_run_ids,
        )

    async def start(self) -> None:
        if not self._enabled or self._audit_mode == "off" or self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="audit-index-refresh")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        self._wake.set()
        try:
            await asyncio.wait_for(self._task, timeout=5)
        except TimeoutError:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        finally:
            self._task = None

    async def request_refresh(self) -> None:
        self._wake.set()

    async def refresh_now(self) -> IndexStatus:
        try:
            await asyncio.to_thread(self.indexer.update)
            self._status = await asyncio.to_thread(self.read_service.status_from_index)
        except IndexRebuildRequired:
            self._status = IndexStatus(
                state="unavailable",
                last_error=SanitizedIndexError(
                    code="index_rebuild_required", message="Audit index rebuild required."
                ),
            )
        except Exception as error:
            previous = self._status
            self._status = previous.model_copy(
                update={
                    "state": "stale" if previous.revision is not None else "unavailable",
                    "last_error": SanitizedIndexError(
                        code="index_refresh_failed", message=type(error).__name__
                    ),
                }
            )
            if self._logger is not None:
                self._logger.warning("audit index refresh failed: {}", type(error).__name__)
        return self.snapshot_status()

    def snapshot_status(self) -> IndexStatus:
        return self._status.model_copy(deep=True)

    async def _run(self) -> None:
        while not self._stop.is_set():
            await self.refresh_now()
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.refresh_interval)
            except TimeoutError:
                pass
