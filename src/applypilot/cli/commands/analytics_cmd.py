"""CLI command: analytics — pipeline analytics and insights."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

console = Console()


def report() -> None:
    """Show analytics summary: skill gaps, effectiveness, pool, market, health, roadmap."""
    from applypilot.bootstrap import get_app
    from applypilot.analytics.aggregators import (
        CareerHealthReport,
        CareerRoadmapReport,
        EffectivenessReport,
        MarketIntelligenceReport,
        PoolSegmentationReport,
        SkillGapReport,
        TailoringReport,
        TrackComparisonReport,
        LatencyReport,
        generate_summary,
        process_event,
    )

    app = get_app()
    repo = app.container.analytics_repo

    skill_gaps = SkillGapReport()
    effectiveness = EffectivenessReport()
    pool = PoolSegmentationReport()
    market = MarketIntelligenceReport()
    health = CareerHealthReport()
    roadmap = CareerRoadmapReport()
    tracks = TrackComparisonReport()
    tailoring = TailoringReport()
    latency = LatencyReport()

    events = repo.get_unprocessed(limit=10000)
    for event in events:
        process_event(
            event.event_type,
            event.payload,
            skill_gaps=skill_gaps,
            effectiveness=effectiveness,
            pool=pool,
            market=market,
            health=health,
            roadmap=roadmap,
            tracks=tracks,
            tailoring=tailoring,
            latency=latency,
        )

    summary = generate_summary(
        skill_gaps,
        effectiveness,
        pool,
        market=market,
        health=health,
        roadmap=roadmap,
        tracks=tracks,
        tailoring=tailoring,
        latency=latency,
    )

    # Skill gaps
    if gaps := summary["skill_gaps"]["top_missing"]:
        table = Table(title=f"Top Missing Skills ({summary['skill_gaps']['jobs_analyzed']} jobs analyzed)")
        table.add_column("Skill", style="bold")
        table.add_column("Frequency", justify="right")
        for skill, count in gaps:
            table.add_row(skill, str(count))
        console.print(table)
    else:
        console.print("[dim]No skill gap data yet. Run scoring first.[/dim]")

    # Level Strategy Distribution (from evaluation reports)
    level_counts: dict[str, int] = {}
    for event in events:
        if event.event_type == "job_scored":
            import json

            try:
                payload = json.loads(event.payload) if isinstance(event.payload, str) else event.payload
                strategy = payload.get("level_strategy", "")
                if strategy:
                    level_counts[strategy] = level_counts.get(strategy, 0) + 1
            except (json.JSONDecodeError, TypeError):
                pass
    if level_counts:
        table = Table(title="Level Strategy Distribution")
        table.add_column("Strategy", style="bold")
        table.add_column("Count", justify="right")
        for strategy, count in sorted(level_counts.items(), key=lambda x: -x[1]):
            color = {"stretch": "yellow", "natural_next": "cyan", "level_match": "green", "overqualified": "red"}.get(
                strategy, "white"
            )
            table.add_row(f"[{color}]{strategy}[/{color}]", str(count))
        console.print(table)

    # Effectiveness
    if rates := summary["effectiveness"]["success_rate_by_tier"]:
        table = Table(title="Apply Success Rate by Tier")
        table.add_column("Tier", style="bold")
        table.add_column("Rate", justify="right")
        table.add_column("Details")
        for tier, rate in sorted(rates.items()):
            counts = summary["effectiveness"]["by_tier"].get(tier, {})
            detail = ", ".join(f"{k}={v}" for k, v in counts.items())
            table.add_row(tier, f"{rate:.0%}", detail)
        console.print(table)

    # Pool
    if summary["pool"]["total"]:
        console.print(f"\n[bold]Job Pool:[/bold] {summary['pool']['total']} jobs")
        if bands := summary["pool"]["by_score_band"]:
            for band, count in sorted(bands.items()):
                console.print(f"  {band}: {count}")

    # Market Intelligence (ANALYZE-03)
    if mkt := summary.get("market"):
        if mkt["total_jobs"]:
            console.print(f"\n[bold]Market Intelligence:[/bold] {mkt['total_jobs']} jobs analyzed")
            if skills := mkt["top_skills"]:
                table = Table(title="Most In-Demand Skills")
                table.add_column("Skill", style="bold")
                table.add_column("Mentions", justify="right")
                for skill, count in skills[:15]:
                    table.add_row(skill, str(count))
                console.print(table)
            if locs := mkt["top_locations"]:
                console.print("\n[bold]Top Locations:[/bold]")
                for loc, count in locs:
                    console.print(f"  {loc}: {count}")

    # Career Health Score (ANALYZE-04)
    if ch := summary.get("career_health"):
        score = ch["score"]
        color = "green" if score >= 7 else "yellow" if score >= 5 else "red"
        console.print(f"\n[bold]Career Health Score:[/bold] [{color}]{score}/10[/{color}]")
        console.print(f"  Based on {ch['skill_data_points']} scored jobs")

    # Career Roadmap (ANALYZE-05)
    if rm := summary.get("roadmap"):
        if milestones := rm["milestones"]:
            console.print(f"\n[bold]Career Roadmap:[/bold] ({rm['total_jobs']} jobs analyzed)")
            table = Table(title="Skill Acquisition Priorities")
            table.add_column("Skill", style="bold")
            table.add_column("Demand %", justify="right")
            table.add_column("Priority")
            for m in milestones:
                color = {"high": "red", "medium": "yellow", "low": "dim"}.get(m["priority"], "white")
                table.add_row(m["skill"], f"{m['demand_pct']}%", f"[{color}]{m['priority']}[/{color}]")
            console.print(table)
        if strengths := rm["strengths"]:
            console.print("\n[bold]Your Strengths:[/bold]")
            for skill, count in strengths:
                console.print(f"  [green]✓[/green] {skill} (matched {count}x)")

    # Track Comparison (ANALYZE-07)
    if tc := summary.get("track_comparison"):
        if tc:
            table = Table(title="Track Performance")
            table.add_column("Track", style="bold")
            table.add_column("Jobs", justify="right")
            table.add_column("High Fit", justify="right")
            table.add_column("Avg Score", justify="right")
            table.add_column("Health")
            for seg in tc[:10]:
                avg = seg["avg_score"]
                high = seg["high_fit_jobs"]
                total = seg["total_jobs"]
                # Health assessment
                if avg >= 7 and high >= 3:
                    health = "[green]strong[/green]"
                elif avg >= 5 or high >= 1:
                    health = "[yellow]moderate[/yellow]"
                else:
                    health = "[red]weak — consider dropping[/red]"
                table.add_row(
                    seg["segment"],
                    str(total),
                    str(high),
                    str(avg),
                    health,
                )
            console.print(table)

            # Actionable suggestions
            weak_tracks = [s for s in tc if s["avg_score"] < 5 and s["total_jobs"] >= 3]
            strong_tracks = [s for s in tc if s["avg_score"] >= 7]
            if weak_tracks:
                console.print("\n[yellow]Suggestions:[/yellow]")
                for wt in weak_tracks:
                    console.print(
                        f"  [yellow]→[/yellow] '{wt['segment']}' has avg score {wt['avg_score']} "
                        f"across {wt['total_jobs']} jobs — consider deactivating or strengthening this track"
                    )
            if strong_tracks:
                console.print(
                    f"\n[green]Focus on:[/green] {', '.join(s['segment'] for s in strong_tracks[:3])} "
                    f"— these tracks have the best job fit"
                )

    if not summary["pool"]["total"]:
        console.print("[dim]No data yet. Run the pipeline first.[/dim]")

    # Tailoring Pipeline Stats
    if tl := summary.get("tailoring"):
        if tl["total_jobs"]:
            console.print(f"\n[bold]Tailoring Pipeline:[/bold] {tl['total_jobs']} jobs tailored")
            for pipeline, count in tl["by_pipeline"].items():
                console.print(f"  {pipeline}: {count}")
            console.print(f"  Cache hit rate: {tl['cache_hit_rate']:.0%}")
            console.print(f"  Total overlays stored: {tl['total_overlays']}")

    # Latency
    if lat := summary.get("latency"):
        if llm := lat.get("llm"):
            console.print(
                f"\n[bold]LLM Latency:[/bold] {llm['count']} calls — p50={llm['p50']:.0f}ms p95={llm['p95']:.0f}ms p99={llm['p99']:.0f}ms"
            )
