"""
Automated check on session 2 transcript: proves tool-vs-memory discipline.

The eval criterion hardest to verify by eye: did the agent actually fetch fresh
state in Session 2, or did it quote stale numbers from memory? This script runs
6 assertions against the S2 transcript and prints per-check status so the result
is visible at a glance, not just an exit code.

Usage: python eval.py
Exit 0 = all PASS, 1 = any FAIL.
"""

import re
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

LOG = Path("transcripts/session2.log")
S2_BALANCE_MARKERS = ("99,820", "99820")
S1_BALANCE_MARKER = "128,000"


def run_checks(text: str) -> list[tuple[str, bool, str]]:
    """Returns list of (label, passed, detail_if_failed)."""
    assistant_text = "\n".join(re.findall(r"\[assistant text\][^\[]*", text))
    checks: list[tuple[str, bool, str]] = []

    checks.append((
        "Session briefing fires before first user turn",
        "[SESSION BRIEFING]" in text,
        "[SESSION BRIEFING] block missing — memory-awareness header not emitted",
    ))
    checks.append((
        "Agent calls get_account_balance (no stale recall)",
        "get_account_balance" in text,
        "get_account_balance tool_use not found — agent may be recalling from memory",
    ))
    checks.append((
        "Agent calls get_upcoming_bills (full affordability check)",
        "get_upcoming_bills" in text,
        "get_upcoming_bills tool_use not found — incomplete discretionary-purchase frame",
    ))
    checks.append((
        "Response quotes fresh S2 balance (₹99,820)",
        any(m in text for m in S2_BALANCE_MARKERS),
        f"S2 transcript does not contain any of {S2_BALANCE_MARKERS}",
    ))
    checks.append((
        "Response does NOT quote stale S1 balance (₹128,000)",
        S1_BALANCE_MARKER not in assistant_text,
        f"Assistant text contains stale S1 balance '{S1_BALANCE_MARKER}' — tool-vs-memory discipline broken",
    ))
    checks.append((
        "₹30,000 commitment recalled from S1 memory",
        "30,000" in text or "30000" in text,
        "No reference to the ₹30,000 commitment from S1 — cross-session memory continuity broken",
    ))
    return checks


def main() -> int:
    if not LOG.exists():
        print(f"FAIL: {LOG} not found. Run `python agent.py --session 2` first.")
        return 1
    text = LOG.read_text(encoding="utf-8")
    checks = run_checks(text)

    print("=== goreach.finance agent — Session 2 eval ===")
    width = max(len(label) for label, _, _ in checks) + 2
    for label, passed, _ in checks:
        status = "PASS" if passed else "FAIL"
        dots = "." * max(2, 60 - len(label))
        print(f"[{status}] {label} {dots} {status}")
    print()

    failures = [(label, detail) for label, passed, detail in checks if not passed]
    if failures:
        print(f"{len(failures)}/{len(checks)} checks failed:")
        for label, detail in failures:
            print(f"  - {label}: {detail}")
        return 1
    print(f"All {len(checks)} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
