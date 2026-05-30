"""
Persistent memory for the finance companion agent.

Principle: memory holds INTENT (commitments, acknowledged patterns, profile, reminders).
Tools hold FACTS (balances, transactions, bills). Anything that can change between
Monday and Thursday must be fetched live, never recalled.

Every entry carries provenance — `confidence: "stated_by_user" | "inferred_by_agent"` —
so future reads can reason about whether a fact came from the user's mouth or the
agent's projection. This is the defense against memory poisoning.
"""

import json
import os
import sys
from copy import deepcopy

DEFAULT_MEMORY = {
    "schema_version": "1.0",
    "user_profile": {
        "name": "Priya Sharma",
        "age": 28,
        "city": "Bangalore",
        "monthly_income_inr": 120000,
        "salary_credited_on": "1st of each month",
        "stated_goal": "Save ₹15 lakh in 2 years for a house down payment in Bangalore",
    },
    "commitments": [],
    "acknowledged_patterns": [],
    "reminders_set": [],
    "profile_updates": [],
    "session_log": [],
}

VALID_KINDS = ("commitment", "acknowledged_pattern", "profile_update")
VALID_CONFIDENCE = ("stated_by_user", "inferred_by_agent")


class Memory:
    def __init__(self, data: dict):
        self.data = data

    @classmethod
    def load(cls, path: str) -> "Memory":
        if not os.path.exists(path):
            return cls(deepcopy(DEFAULT_MEMORY))
        try:
            with open(path, "r", encoding="utf-8") as f:
                return cls(json.load(f))
        except (json.JSONDecodeError, OSError) as e:
            print(f"[memory] WARNING: {path} unreadable ({e}); starting from DEFAULT_MEMORY", file=sys.stderr)
            return cls(deepcopy(DEFAULT_MEMORY))

    def save(self, path: str) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)

    def first_name(self) -> str:
        return self.data["user_profile"]["name"].split()[0]

    def remember(self, kind: str, summary: str, details: dict, confidence: str,
                 session_id: int, date: str) -> dict:
        if kind not in VALID_KINDS:
            return {"status": "rejected", "reason": f"unknown kind '{kind}' (valid: {VALID_KINDS})"}
        if confidence not in VALID_CONFIDENCE:
            return {"status": "rejected", "reason": f"unknown confidence '{confidence}' (valid: {VALID_CONFIDENCE})"}
        entry = {
            "summary": summary,
            "details": details,
            "confidence": confidence,
            "source_session": session_id,
            "created_at": date,
        }
        if kind == "commitment":
            # Dedup on summary match against active commitments — keeps re-runs clean.
            existing = next((c for c in self.data["commitments"]
                             if c.get("status") == "active" and c.get("summary") == summary), None)
            if existing is not None:
                return {"status": "stored", "kind": kind, "id": existing["id"], "deduped": True}
            entry["id"] = f"cmt_{len(self.data['commitments']) + 1:03d}"
            entry["status"] = "active"
            self.data["commitments"].append(entry)
        elif kind == "acknowledged_pattern":
            entry["id"] = f"pat_{len(self.data['acknowledged_patterns']) + 1:03d}"
            self.data["acknowledged_patterns"].append(entry)
        else:  # profile_update
            field, value = details.get("field"), details.get("value")
            if field is None:
                return {"status": "rejected", "reason": "profile_update requires details.field"}
            prev_value = self.data["user_profile"].get(field)
            self.data["user_profile"][field] = value
            # Append an audit entry so provenance survives the mutation. Storing prev_value
            # makes the change reversible and lets a future read explain what changed.
            updates = self.data.setdefault("profile_updates", [])
            entry["details"] = {"field": field, "from": prev_value, "to": value}
            entry["id"] = f"prof_{field}_{len(updates) + 1:03d}"
            updates.append(entry)
        return {"status": "stored", "kind": kind, "id": entry["id"]}

    def record_reminder(self, reminder_result: dict, session_id: int, date: str) -> None:
        # tools.set_reminder() returns {"status": "set", ...}; we override to "pending" explicitly
        # instead of relying on dict-literal-order semantics.
        payload = {k: v for k, v in reminder_result.items() if k != "status"}
        payload.update({"source_session": session_id, "created_at": date, "status": "pending"})
        self.data["reminders_set"].append(payload)

    def close_session(self, session_id: int, date: str,
                      pre_counts: dict[str, int]) -> None:
        """Derive a one-line summary from what was actually written this session."""
        singular = {"commitments": "commitment", "patterns": "pattern", "reminders": "reminder"}
        deltas = {
            "commitments": len(self.data["commitments"]) - pre_counts["commitments"],
            "patterns":    len(self.data["acknowledged_patterns"]) - pre_counts["patterns"],
            "reminders":   len(self.data["reminders_set"]) - pre_counts["reminders"],
        }
        parts = [f"{v} new {singular[k] if v == 1 else k}" for k, v in deltas.items() if v > 0] or ["no new state"]
        self.data["session_log"].append({
            "session_id": session_id,
            "date": date,
            "summary": "; ".join(parts),
        })

    def counts(self) -> dict[str, int]:
        """Snapshot list lengths so close_session() can diff against them."""
        return {
            "commitments": len(self.data["commitments"]),
            "patterns":    len(self.data["acknowledged_patterns"]),
            "reminders":   len(self.data["reminders_set"]),
        }

    def active_commitments(self) -> list[dict]:
        return [c for c in self.data["commitments"] if c.get("status") == "active"]

    def pending_reminders(self) -> list[dict]:
        return [r for r in self.data["reminders_set"] if r.get("status") == "pending"]

    def to_prompt_block(self) -> str:
        """Render memory state for injection under the system prompt's
        '## What I Know About {name}' section. Each entry shows ID + provenance
        + date so the model can cite specifics; see the hedge comment below for
        how inferred entries are visually distinguished."""
        p = self.data["user_profile"]
        lines = [
            f"- User: {p['name']}, age {p['age']}, {p['city']}.",
            f"- Stated goal: {p['stated_goal']}.",
            f"- Monthly post-tax income: ₹{p['monthly_income_inr']:,} (credited {p['salary_credited_on']}).",
        ]
        # Render-time hedge on inferred entries. Without a visual signal, the model
        # has no in-prompt way to weight a prior agent guess differently from a user
        # statement — provenance becomes a label only. The "(UNVERIFIED INFERENCE)"
        # prefix is the cheapest behavioral cue: paired with the constitution rule
        # on provenance, it makes the tag a consumed mechanism, not just metadata.
        hedge = lambda confidence: "(UNVERIFIED INFERENCE) " if confidence == "inferred_by_agent" else ""
        active = self.active_commitments()
        if active:
            lines.append("\nActive commitments (made by the user in prior sessions):")
            for c in active:
                lines.append(f"  * [{c['id']}, {c['confidence']}, {c['created_at']}] {hedge(c['confidence'])}{c['summary']}")
        patterns = self.data["acknowledged_patterns"]
        if patterns:
            lines.append("\nPatterns the user has acknowledged:")
            for pat in patterns:
                lines.append(f"  * [{pat['id']}, {pat['confidence']}] {hedge(pat['confidence'])}{pat['summary']}")
        pending = self.pending_reminders()
        if pending:
            lines.append("\nReminders the user has set:")
            for r in pending:
                lines.append(f"  * {r['date']}: {r['content']}")
        if self.data["session_log"]:
            last = self.data["session_log"][-1]
            lines.append(f"\nPrior sessions: {len(self.data['session_log'])} (most recent {last['date']} — {last['summary']}).")
        else:
            lines.append("\nThis is your first conversation with this user.")
        return "\n".join(lines)

    @classmethod
    def validate(cls, data: dict) -> list[str]:
        errors = []
        for key in DEFAULT_MEMORY:
            if key not in data:
                errors.append(f"missing top-level key: {key}")
        for c in data.get("commitments", []):
            for f in ("id", "summary", "confidence", "source_session", "created_at", "status"):
                if f not in c:
                    errors.append(f"commitment {c.get('id', '?')} missing field: {f}")
            if c.get("confidence") not in VALID_CONFIDENCE:
                errors.append(f"commitment {c.get('id')} bad confidence: {c.get('confidence')}")
        return errors
