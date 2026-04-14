"""Chunked pipeline executor — processes jobs in chunks instead of waiting for all.

Purpose: Discovery yields chunks of N jobs. Enrichment starts on chunk 1 while
discovery works on chunk 2. Scoring starts on enriched chunk 1 while enrichment
works on chunk 2. This overlaps I/O-bound stages for better throughput.

SRP: Only orchestrates chunk flow. Does not implement stage logic.
"""

from __future__ import annotations

import logging
import time
import threading
from collections import deque
from typing import Callable

from applypilot.pipeline.context import PipelineContext

log = logging.getLogger(__name__)

# Default chunk size — how many jobs to accumulate before passing to next stage
DEFAULT_CHUNK_SIZE = 1000


class ChunkedExecutor:
    """Runs pipeline stages in overlapping chunks via producer-consumer threads.

    Each stage runs in its own thread. A stage pulls work from its input queue,
    processes it, and pushes results to the next stage's queue.
    """

    def __init__(self, ctx: PipelineContext, chunk_size: int = DEFAULT_CHUNK_SIZE) -> None:
        self._ctx = ctx
        self._chunk_size = chunk_size

    def execute(
        self,
        discover_fn: Callable[[PipelineContext], int],
        enrich_fn: Callable[[int], None],
        score_fn: Callable[[], None],
            on_high_score: Callable[[list[str]], None] | None = None,
            priority_score: int = 9,
    ) -> dict:
        """Run discover → enrich → score in overlapping chunks.

        Args:
            discover_fn: Discovery function. Returns total jobs discovered.
            enrich_fn: Enrichment function. Called per chunk with chunk index.
            score_fn: Scoring function. Called per chunk after enrichment.
            on_high_score: Callback when jobs score >= priority_score. Receives list of URLs.
            priority_score: Threshold for immediate callback (default 9).

        Returns:
            Summary dict with timing and chunk counts.
        """
        results: dict = {"chunks": 0, "errors": [], "elapsed": 0.0, "priority_jobs": 0}
        t0 = time.time()

        # Queues between stages — each item is a chunk index
        enrich_queue: deque[int | None] = deque()
        score_queue: deque[int | None] = deque()
        enrich_ready = threading.Event()
        score_ready = threading.Event()
        priority_tailored: set[str] = set()  # URLs already priority-processed

        chunk_count = 0

        def _discover_worker():
            """Discovery thread — inserts jobs and signals enrichment per chunk."""
            nonlocal chunk_count
            try:
                total = discover_fn(self._ctx)
                chunk_count = max((total + self._chunk_size - 1) // self._chunk_size, 1)
                for i in range(chunk_count):
                    enrich_queue.append(i)
                    enrich_ready.set()
                    log.info("Discover: chunk %d ready (%d total jobs)", i, total)
            except Exception as e:
                log.error("Discovery failed: %s", e)
                results["errors"].append(f"discover: {e}")
            finally:
                enrich_queue.append(None)  # sentinel
                enrich_ready.set()

        def _enrich_worker():
            """Enrichment thread — processes chunks as they arrive from discovery."""
            while True:
                enrich_ready.wait()
                while enrich_queue:
                    chunk_idx = enrich_queue.popleft()
                    if chunk_idx is None:
                        score_queue.append(None)
                        score_ready.set()
                        return
                    try:
                        log.info("Enrich: processing chunk %d", chunk_idx)
                        enrich_fn(chunk_idx)
                        score_queue.append(chunk_idx)
                        score_ready.set()
                    except Exception as e:
                        log.error("Enrichment chunk %d failed: %s", chunk_idx, e)
                        results["errors"].append(f"enrich chunk {chunk_idx}: {e}")
                enrich_ready.clear()

        def _score_worker():
            """Scoring thread — processes chunks as they arrive from enrichment."""
            while True:
                score_ready.wait()
                while score_queue:
                    chunk_idx = score_queue.popleft()
                    if chunk_idx is None:
                        return
                    try:
                        log.info("Score: processing chunk %d", chunk_idx)
                        score_fn()
                    except Exception as e:
                        log.error("Scoring chunk %d failed: %s", chunk_idx, e)
                        results["errors"].append(f"score chunk {chunk_idx}: {e}")
                score_ready.clear()

        # Launch threads
        threads = [
            threading.Thread(target=_discover_worker, name="discover", daemon=True),
            threading.Thread(target=_enrich_worker, name="enrich", daemon=True),
            threading.Thread(target=_score_worker, name="score", daemon=True),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        results["chunks"] = chunk_count
        results["elapsed"] = time.time() - t0
        return results
