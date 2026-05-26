"""
Schema sanity test for memory.py. Standalone, no network.

Exercises each `remember` kind, saves, reloads, re-validates. Catches any drift
between the in-memory Memory class and the on-disk JSON shape.

Usage: python validate_memory.py
"""

import json
import os
import sys
import tempfile

from memory import Memory, VALID_CONFIDENCE

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    failures: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "m.json")
        m = Memory.load(path)  # absent file → DEFAULT

        # Exercise each kind.
        r1 = m.remember("commitment", "Save ₹30K to house fund monthly",
                        {"amount_inr": 30000, "cadence": "monthly", "target": "house_fund"},
                        "stated_by_user", session_id=1, date="2025-11-03")
        r2 = m.remember("acknowledged_pattern", "Food delivery feels too high",
                        {"observation": "user said food delivery is too much"},
                        "stated_by_user", session_id=1, date="2025-11-03")
        r3 = m.remember("profile_update", "Adjusted city",
                        {"field": "city", "value": "Bangalore"},
                        "stated_by_user", session_id=1, date="2025-11-03")
        for r, label in ((r1, "commitment"), (r2, "pattern"), (r3, "profile")):
            if r.get("status") != "stored":
                failures.append(f"{label} not stored: {r}")

        # Reject unknown kind and unknown confidence.
        bad_kind = m.remember("zzz", "x", {}, "stated_by_user", 1, "2025-11-03")
        if bad_kind.get("status") != "rejected":
            failures.append(f"bad kind not rejected: {bad_kind}")
        bad_conf = m.remember("commitment", "x", {}, "guessed", 1, "2025-11-03")
        if bad_conf.get("status") != "rejected":
            failures.append(f"bad confidence not rejected: {bad_conf}")

        # Reminder + session close.
        m.record_reminder({"reminder_id": "rem_1", "date": "2025-11-25",
                           "content": "Transfer ₹30K"}, session_id=1, date="2025-11-03")
        m.close_session(1, "2025-11-03", {"commitments": 0, "patterns": 0, "reminders": 0})

        # Persist, reload, validate.
        m.save(path)
        m2 = Memory.load(path)
        errors = Memory.validate(m2.data)
        if errors:
            failures.extend(f"validate: {e}" for e in errors)

        # Malformed-file fallback.
        with open(path, "w") as f:
            f.write("not json{{")
        m3 = Memory.load(path)
        if m3.data["commitments"] != []:
            failures.append("malformed-file fallback did not return DEFAULT_MEMORY")

        # Provenance sanity.
        for c in m2.data["commitments"]:
            if c.get("confidence") not in VALID_CONFIDENCE:
                failures.append(f"bad provenance on disk: {c}")

        # Render check: the block must use the ₹ glyph for INR figures, not "Rs ".
        block = m2.to_prompt_block()
        if "₹" not in block:
            failures.append("prompt block missing ₹ symbol")
        if "Rs " in block:
            failures.append("prompt block still contains 'Rs ' instead of '₹'")

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"PASS: memory schema OK; provenance enforced; malformed-file fallback works; render uses ₹.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
