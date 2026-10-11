"""Background-task dispatcher adapter.

Wraps Starlette's request-scoped `BackgroundTasks`. The background function
opens its OWN DB session from `SessionLocal` — it must never reuse the
request's session, which is closed when the response is sent.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from starlette.background import BackgroundTasks

logger = logging.getLogger(__name__)


class BackgroundTaskDispatcher:
    """Adapter: schedules a background job via BackgroundTasks."""

    def __init__(self, background: BackgroundTasks) -> None:
        self._background = background

    def enqueue_processing(self, file_id: str, job_factory: Callable[[], Callable[[], None]]) -> None:
        """Schedule a background job for `file_id`.

        `job_factory` is a callable that returns a no-arg function to run in the background.
        The factory is called immediately to capture any dependencies; the returned
        function is what gets added to BackgroundTasks.
        """
        job = job_factory()
        logger.info("Enqueueing background job for file_id=%s", file_id)
        self._background.add_task(job)
        logger.info("Background job added for file_id=%s", file_id)
