"""Tests for apply/scheduler.py — CFS fair job scheduler."""

from __future__ import annotations

from applypilot.apply.scheduler import JobScheduler, Job


def _make_job(company: str, board: str, score: int = 8) -> Job:
    return Job(url=f"{company}-{board}-{score}", title=f"SWE at {company}",
               company=company, board=board, score=score)


class TestJobScheduler:

    def test_empty_scheduler(self):
        s = JobScheduler()
        s.build()
        assert s.next() is None
        assert s.generate_queue() == []

    def test_single_job(self):
        s = JobScheduler()
        s.add(_make_job("Stripe", "greenhouse"))
        s.build()
        j = s.next()
        assert j.company == "Stripe"
        assert s.next() is None

    def test_round_robin_companies(self):
        s = JobScheduler()
        for c in ["Stripe", "Affirm", "Netflix"]:
            s.add(_make_job(c, "greenhouse"))
        s.build()
        queue = s.generate_queue()
        companies = [j.company for j in queue]
        assert len(companies) == 3
        # All unique in first round
        assert len(set(companies)) == 3

    def test_interleave_boards(self):
        s = JobScheduler()
        s.add(_make_job("Stripe", "greenhouse"))
        s.add(_make_job("Motorola", "workday"))
        s.add(_make_job("Infystrat", "hackernews"))
        s.build()
        queue = s.generate_queue()
        boards = [j.board for j in queue]
        # All 3 boards represented
        assert set(boards) == {"greenhouse", "workday", "hackernews"}

    def test_score_priority_within_company(self):
        s = JobScheduler()
        s.add(_make_job("Stripe", "greenhouse", 7))
        s.add(_make_job("Stripe", "greenhouse", 9))
        s.add(_make_job("Stripe", "greenhouse", 8))
        s.build()
        queue = s.generate_queue()
        scores = [j.score for j in queue]
        assert scores == [9, 8, 7]

    def test_one_per_company_before_repeat(self):
        s = JobScheduler()
        for _ in range(3):
            s.add(_make_job("Stripe", "greenhouse"))
        s.add(_make_job("Affirm", "greenhouse"))
        s.build()
        queue = s.generate_queue()
        # First 2 should be different companies
        assert queue[0].company != queue[1].company

    def test_generate_queue_with_limit(self):
        s = JobScheduler()
        for c in ["A", "B", "C", "D", "E"]:
            s.add(_make_job(c, "greenhouse"))
        s.build()
        queue = s.generate_queue(n=3)
        assert len(queue) == 3

    def test_iterator(self):
        s = JobScheduler()
        s.add(_make_job("X", "greenhouse"))
        s.add(_make_job("Y", "workday"))
        s.build()
        jobs = list(s)
        assert len(jobs) == 2

    def test_stats(self):
        s = JobScheduler()
        s.add(_make_job("Stripe", "greenhouse", 8))
        s.add(_make_job("Stripe", "greenhouse", 7))
        s.add(_make_job("Motorola", "workday", 8))
        s.build()
        stats = s.stats()
        assert stats["total_jobs"] == 3
        assert stats["boards"] == 2
        assert stats["companies"] == 2

    def test_weight_higher_score_gets_more_bandwidth(self):
        """Higher score = lower weight = picked sooner in next round."""
        s = JobScheduler()
        # Company A has score 10, Company B has score 5
        for _ in range(3):
            s.add(_make_job("HighScore", "greenhouse", 10))
            s.add(_make_job("LowScore", "greenhouse", 5))
        s.build()
        queue = s.generate_queue()
        # HighScore should appear more in the first half
        first_half = queue[:3]
        high_count = sum(1 for j in first_half if j.company == "HighScore")
        assert high_count >= 2  # At least 2 of first 3


class TestBoardDetection:

    def test_detect_greenhouse(self):
        from applypilot.apply.native_agent import _detect_board
        assert _detect_board({"application_url": "https://boards.greenhouse.io/stripe/jobs/123"}) == "greenhouse"

    def test_detect_embedded_greenhouse(self):
        from applypilot.apply.native_agent import _detect_board
        assert _detect_board({"application_url": "https://stripe.com/jobs?gh_jid=123"}) == "greenhouse"

    def test_detect_workday(self):
        from applypilot.apply.native_agent import _detect_board
        assert _detect_board({"application_url": "https://motorola.wd5.myworkdayjobs.com/jobs/123"}) == "workday"

    def test_detect_lever(self):
        from applypilot.apply.native_agent import _detect_board
        assert _detect_board({"application_url": "https://jobs.lever.co/company/123"}) == "lever"

    def test_detect_linkedin(self):
        from applypilot.apply.native_agent import _detect_board
        assert _detect_board({"application_url": "https://www.linkedin.com/jobs/view/123"}) == "linkedin"

    def test_detect_strategy_fallback(self):
        from applypilot.apply.native_agent import _detect_board
        assert _detect_board({"application_url": "https://custom.com/apply", "strategy": "greenhouse"}) == "greenhouse"

    def test_detect_unknown(self):
        from applypilot.apply.native_agent import _detect_board
        assert _detect_board({"application_url": "https://random-company.com/careers"}) == "unknown"
