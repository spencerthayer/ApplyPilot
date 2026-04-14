"""Agent log validator — post-run regression testing for native agent behavior.

Parses agent log files and checks for known anti-patterns:
- browser_evaluate writes (React ignores them)
- HTML ids used as refs (should be e42-style snapshot refs)
- Wrong browser_tabs param (tab_index instead of index)
- Excessive iterations
"""

from __future__ import annotations

import re
from pathlib import Path


def validate_agent_log(log_path: str | Path) -> dict:
    """Validate an agent log file for behavioral regressions.

    Returns:
        {"iterations": int, "issues": list[str], "passed": bool}
    """
    text = Path(log_path).read_text(encoding="utf-8")
    issues: list[str] = []

    # Check: no browser_evaluate writes
    eval_writes = re.findall(r"browser_evaluate.*?\.value\s*=", text)
    if eval_writes:
        issues.append(f"browser_evaluate wrote to page {len(eval_writes)}x")

    # Check: no HTML ids used as refs (refs should be e\d+)
    bad_refs = re.findall(r'browser_type.*?"ref"\s*:\s*"(?!e\d+")[^"]*"', text)
    if bad_refs:
        issues.append(f"HTML ids used as refs {len(bad_refs)}x")

    # Check: browser_tabs used correct param (index, not tab_index)
    bad_tabs = re.findall(r"tab_index", text)
    if bad_tabs:
        issues.append(f"tab_index used instead of index {len(bad_tabs)}x")

    # Check: no BLOCKED messages (evaluate write was attempted)
    blocked = re.findall(r"BLOCKED.*evaluate", text)
    if blocked:
        issues.append(f"evaluate write attempted (blocked) {len(blocked)}x")

    # Iteration count
    iters = re.findall(r"\[iter (\d+)\]", text)
    max_iter = max(int(i) for i in iters) if iters else 0

    return {
        "iterations": max_iter,
        "issues": issues,
        "passed": len(issues) == 0,
    }
