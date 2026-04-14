"""Fair Job Scheduler — CFS-inspired hierarchical scheduling for job applications.

Produces a FIFO queue that spreads applications across boards and companies
using virtual runtime fairness. Higher-score jobs get more bandwidth.

Usage:
    scheduler = JobScheduler()
    scheduler.load_from_db(min_score=7)
    queue = scheduler.generate_queue(n=50)  # next 50 jobs in optimal order
    # or
    job = scheduler.next()  # single pick, O(log n)

Tree structure:
    Root
    ├── Board: greenhouse (vruntime=0)
    │   ├── Company: Stripe (vruntime=0)
    │   │   ├── Job: Backend Engineer (score=8)
    │   │   └── Job: Android Engineer (score=7)
    │   └── Company: Affirm (vruntime=0)
    │       └── Job: SWE II (score=8)
    ├── Board: workday (vruntime=0)
    │   └── Company: Motorola (vruntime=0)
    │       └── Job: Sr Full Stack (score=8)
    └── Board: hackernews (vruntime=0)
        └── Company: Infystrat (vruntime=0)
            └── Job: Full Stack (score=8)
"""

from __future__ import annotations

import heapq
import logging
from dataclasses import dataclass, field
from typing import Iterator

log = logging.getLogger(__name__)


@dataclass
class Job:
    url: str
    title: str
    company: str
    board: str
    score: int
    location: str = ""

    @property
    def weight(self) -> float:
        """Higher score = lower cost per pick = more bandwidth."""
        return max(1, 11 - self.score)  # score 10 → weight 1, score 7 → weight 4


@dataclass
class CompanyNode:
    name: str
    board: str
    vruntime: float = 0.0
    jobs: list[Job] = field(default_factory=list)
    _idx: int = 0  # next job index (jobs sorted by score desc)

    def has_jobs(self) -> bool:
        return self._idx < len(self.jobs)

    def peek(self) -> Job | None:
        return self.jobs[self._idx] if self.has_jobs() else None

    def pop(self) -> Job | None:
        if not self.has_jobs():
            return None
        job = self.jobs[self._idx]
        self._idx += 1
        self.vruntime += job.weight
        return job

    def __lt__(self, other: CompanyNode) -> bool:
        return self.vruntime < other.vruntime


@dataclass
class BoardNode:
    name: str
    vruntime: float = 0.0
    companies: dict[str, CompanyNode] = field(default_factory=dict)
    _heap: list[CompanyNode] = field(default_factory=list)
    _built: bool = False

    def _build_heap(self) -> None:
        if not self._built:
            self._heap = [c for c in self.companies.values() if c.has_jobs()]
            heapq.heapify(self._heap)
            self._built = True

    def has_jobs(self) -> bool:
        self._build_heap()
        # Clean exhausted companies
        while self._heap and not self._heap[0].has_jobs():
            heapq.heappop(self._heap)
        return bool(self._heap)

    def pick(self) -> Job | None:
        """Pick next job from the least-served company."""
        self._build_heap()
        while self._heap:
            company = self._heap[0]
            if not company.has_jobs():
                heapq.heappop(self._heap)
                continue
            job = company.pop()
            self.vruntime += job.weight
            # Re-heapify after vruntime update
            heapq.heapreplace(self._heap, company)
            return job
        return None

    def __lt__(self, other: BoardNode) -> bool:
        return self.vruntime < other.vruntime


class JobScheduler:
    """CFS-inspired fair scheduler. Spreads jobs across boards and companies."""

    def __init__(self) -> None:
        self.boards: dict[str, BoardNode] = {}
        self._heap: list[BoardNode] = []
        self._total: int = 0

    def add(self, job: Job) -> None:
        """Add a job to the scheduler tree."""
        board = self.boards.get(job.board)
        if not board:
            board = BoardNode(name=job.board)
            self.boards[job.board] = board

        company = board.companies.get(job.company)
        if not company:
            company = CompanyNode(name=job.company, board=job.board)
            board.companies[job.company] = company

        company.jobs.append(job)
        self._total += 1

    def build(self) -> None:
        """Sort jobs within each company and build heaps. Call after all adds."""
        for board in self.boards.values():
            for company in board.companies.values():
                company.jobs.sort(key=lambda j: -j.score)
            board._built = False
        self._heap = [b for b in self.boards.values() if b.has_jobs()]
        heapq.heapify(self._heap)
        log.info(
            "[scheduler] Built: %d jobs, %d boards, %d companies",
            self._total,
            len(self.boards),
            sum(len(b.companies) for b in self.boards.values()),
        )

    def next(self) -> Job | None:
        """Pick the next job. O(log n). Returns None when exhausted."""
        while self._heap:
            board = self._heap[0]
            if not board.has_jobs():
                heapq.heappop(self._heap)
                continue
            job = board.pick()
            if job:
                heapq.heapreplace(self._heap, board)
                return job
            heapq.heappop(self._heap)
        return None

    def generate_queue(self, n: int = 0) -> list[Job]:
        """Generate a FIFO queue of n jobs (0 = all). Returns list in order."""
        result = []
        limit = n if n > 0 else self._total
        for _ in range(limit):
            job = self.next()
            if not job:
                break
            result.append(job)
        return result

    def __iter__(self) -> Iterator[Job]:
        """Iterate over all jobs in fair order."""
        while True:
            job = self.next()
            if not job:
                return
            yield job

    def load_from_db(self, min_score: int = 7) -> int:
        """Load eligible jobs from the database into the scheduler.

        Returns number of jobs loaded.
        """
        from applypilot.bootstrap import get_app
        import dataclasses

        job_repo = get_app().container.job_repo
        rows = job_repo.get_jobs_by_stage_dict(stage="ready_to_apply", min_score=min_score)

        for row in rows:
            r = dataclasses.asdict(row) if not isinstance(row, dict) else row
            if not r.get("tailored_resume_path"):
                continue
            status = (r.get("apply_status") or "").lower()
            if status in ("applied", "in_progress", "needs_human"):
                continue

            self.add(Job(
                url=r["url"],
                title=r.get("title", ""),
                company=r.get("site", "unknown"),
                board=r.get("strategy", "unknown"),
                score=r.get("fit_score") or 0,
                location=r.get("location", ""),
            ))

        self.build()
        return self._total

    def stats(self) -> dict:
        """Return scheduler statistics."""
        board_stats = {}
        for name, board in self.boards.items():
            companies = {}
            for cname, company in board.companies.items():
                remaining = len(company.jobs) - company._idx
                if remaining > 0:
                    companies[cname] = {
                        "total": len(company.jobs),
                        "remaining": remaining,
                        "top_score": company.jobs[0].score if company.jobs else 0,
                        "vruntime": round(company.vruntime, 1),
                    }
            if companies:
                board_stats[name] = {
                    "companies": len(companies),
                    "jobs": sum(c["remaining"] for c in companies.values()),
                    "vruntime": round(board.vruntime, 1),
                    "detail": companies,
                }
        return {
            "total_jobs": self._total,
            "boards": len(board_stats),
            "companies": sum(b["companies"] for b in board_stats.values()),
            "by_board": board_stats,
        }
