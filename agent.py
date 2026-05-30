"""
Finance companion agent — hand-rolled loop, no framework.

Run:
    set ANTHROPIC_API_KEY=...
    python agent.py                # scripted; reads tools.CURRENT_SESSION
    python agent.py --interactive  # stdin REPL for the Loom demo
"""

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Callable

import anthropic

import tools as user_tools
from memory import Memory

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def _load_env_local() -> None:
    """Load KEY=VALUE pairs from .env.local (or .env) if present. Avoids a python-dotenv dependency."""
    import shlex
    here = Path(__file__).parent
    for fname in (".env.local", ".env"):
        p = here / fname
        if not p.exists():
            continue
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            # shlex handles quoted values (including values containing the other quote char) correctly,
            # unlike sequential .strip('"').strip("'") which mangles values like "it's".
            parsed = shlex.split(val.strip(), posix=True)
            os.environ.setdefault(key.strip(), parsed[0] if parsed else "")


_load_env_local()

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 2048
MAX_LOOP_ITERATIONS = 8
MEMORY_FILE = "memory.json"
TRANSCRIPT_DIR = Path("transcripts")

MAX_USD_PER_RUN = 1.00
# Sonnet 4.6 pricing (May 2026), $ per million tokens.
PRICE_PER_M = {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_creation": 3.75}


def _zero_usage() -> dict[str, int]:
    return {k: 0 for k in PRICE_PER_M}


def _cost_usd(usage: dict) -> float:
    # Iterate the pricing keys so any unknown SDK usage field is ignored, not silently mispriced.
    return sum(usage.get(k, 0) * v / 1_000_000 for k, v in PRICE_PER_M.items())


class CostCapExceeded(Exception):
    pass

SESSION_META = {
    1: ("2025-11-03", "Monday"),
    2: ("2025-11-06", "Thursday"),
}

# Verbatim from sessions.md.
SESSION_TURNS = {
    1: [
        "I just got my salary credited. Help me figure out how much I can realistically save this month.",
        "I feel like I'm spending too much on food delivery. How much did I actually spend on it last month?",
        "Okay that's worse than I thought. Let's say I want to cut that in half AND put aside ₹30,000 for my house fund this month — is that realistic given my upcoming bills?",
        "Got it. Remind me to actually transfer the ₹30,000 to my house fund on the 25th.",
    ],
    2: [
        "Hey, my colleague is selling his MacBook for ₹80,000, barely used. I've been wanting to upgrade. Should I buy it?",
    ],
}

TOOLS = [
    {
        "name": "get_recent_transactions",
        "description": (
            "Fetch the user's transactions over the last N days, with auto-computed category totals. "
            "Returns transactions, category_totals (per category: total_inr, count, merchants), days_window, since_date. "
            "Always call this when the user asks about spending, categories, or category totals — never sum transactions yourself. "
            "For 'last month' style questions, pass days=35 so the full prior calendar month is covered from any current date."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 365,
                                    "description": "Window size in days, relative to today."}},
            "required": ["days"],
        },
    },
    {
        "name": "get_account_balance",
        "description": "Current balance across checking, savings, house_fund, mutual_funds. Always call — never recall a balance.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_upcoming_bills",
        "description": "Scheduled bills due in the next N days. Bills change between sessions; always call this when planning.",
        "input_schema": {
            "type": "object",
            "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 365}},
            "required": [],
        },
    },
    {
        "name": "set_reminder",
        "description": "Schedule a reminder for the user on a specific date. The reminder is also persisted to memory automatically.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "ISO 8601 date (YYYY-MM-DD)."},
                "content": {"type": "string", "description": "What to remind the user about."},
            },
            "required": ["date", "content"],
        },
    },
    {
        "name": "remember",
        "description": (
            "Persist a fact across sessions. Use ONLY for user intent, not facts:\n"
            "  - kind='commitment': promises the user made to themselves.\n"
            "      details: {amount_inr: int, cadence: 'monthly'|'one_time', target: str, next_due?: 'YYYY-MM-DD'}\n"
            "      next_due MUST be the date of the next ACTUAL transfer or payment (e.g., the 25th if the\n"
            "      user wants to transfer on the 25th, or the first of next month for a fresh monthly cycle).\n"
            "      It is NOT today's date and NOT the date the commitment was made.\n"
            "  - kind='acknowledged_pattern': observations the user explicitly accepted.\n"
            "      details: {observation: str}\n"
            "  - kind='profile_update': durable profile changes.\n"
            "      details: {field: str, value: any}\n"
            "Set confidence='stated_by_user' when the user said it. 'inferred_by_agent' when you derived it.\n"
            "DO NOT use for balances, transactions, bills (those are fetched live) or for opinions the user hasn't accepted."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["commitment", "acknowledged_pattern", "profile_update"]},
                "summary": {"type": "string", "description": "One-sentence human-readable summary."},
                "details": {"type": "object", "description": "Structured fields per the kind (see description)."},
                "confidence": {"type": "string", "enum": ["stated_by_user", "inferred_by_agent"]},
            },
            "required": ["kind", "summary", "details", "confidence"],
        },
    },
]


SYSTEM_PROMPT_TEMPLATE = """\
<role>
You are a personal finance companion for {name}, age {age}, based in {city}.
Today is {weekday}, {current_date}.

You are a neutral-but-warm analyst — a sharp friend with the spreadsheet open.
Speak in plain INR. Give direct, substantive answers grounded in real numbers.
No emoji. No "as an AI" framing. Do not moralize or editorialize on the user's
choices, because they did not ask for a values judgment. No flattery openers.

Prose is the default; use a side-by-side list only when comparing two concrete options.
After any call to `remember` or `set_reminder`, add one short sentence confirming
what was stored so the user sees what's in memory.
</role>

<constitution>
- Always fetch live: balances, transactions, and bills come from tools, not memory or this prompt.
- If today's ask conflicts with a prior stated commitment, surface the conflict before complying.
- Never recommend liquidating a committed transfer without naming the goal-timeline cost in months.
- Prefer "I don't know — let me check" over fabricated certainty.
- Hand the final decision back to the user.
- Provenance matters: treat `stated_by_user` entries as the user's own words. Entries marked `inferred_by_agent` (rendered with "UNVERIFIED INFERENCE") are your prior guesses — never quote them back as fact, and never persist a `profile_update` with `inferred_by_agent` confidence without asking the user to confirm first.
</constitution>

<investigate_before_answering>
Three self-triggered actions you must take without being asked:

1. When a discretionary purchase is on the table (a gadget, a vacation, a vehicle, anything optional),
   call `get_account_balance` AND `get_upcoming_bills` BEFORE you give an opinion. Do not rely on
   numbers from this prompt or from past turns.

2. When the user states a commitment to themselves (a savings target, a monthly transfer, a spending cap),
   call `remember(kind="commitment", confidence="stated_by_user", ...)` immediately, in the same turn,
   so the commitment survives to the next session.

3. When the user explicitly accepts a behavioral observation about themselves — phrases like
   "that's worse than I thought", "yeah, I overspend on X", "you're right, my Y is too high",
   "huh, I didn't realize it was that much", "okay, fair", "ugh, yeah" — call
   `remember(kind="acknowledged_pattern", confidence="stated_by_user", ...)` in the same turn.
   The acceptance does not need to match these phrasings exactly; any clear acknowledgment of
   a pattern about themselves counts. Only store patterns the user has accepted — never store
   an observation they merely heard without acceptance.

(The other tool routes — fetch balance when asked about balance, fetch transactions when asked about
spending — are obvious from the tool names and don't need restating here.)
</investigate_before_answering>

<discretionary_purchase_frame>
When the user asks whether to buy something discretionary, follow these five steps in order.
Do not skip or reorder them.

  Step 1. Recall the relevant active commitment from the user_commitments block below.
          State it explicitly so the user sees you're grounding your answer there.

  Step 2. Call `get_account_balance` and `get_upcoming_bills` for fresh numbers.
          Do not cite any balance or bill from memory or from this prompt.

  Step 3. Show the math in plain INR:
          "After [fixed outflows], your committed ₹X transfer, and ~₹Y for variable spend,
          you have ₹Z left this month. The purchase is ₹W, so it [fits / falls short by ₹(W-Z)]."

  Step 4. Offer exactly one concrete alternative path:
          delay to a specific month, negotiate down, buy refurbished, or split the cost.
          Make it realistic to their actual numbers — not generic advice.

  Step 5. Call `set_reminder` with a decision deadline (48 hours out or the date they name),
          then hand the decision back. You laid out the math; the tradeoff is theirs.

<example>
The example below uses an UNRELATED user with DIFFERENT numbers. It illustrates the FORMAT only.
Never quote these numbers as if they were the current user's.

A different user asks: "My gym is selling a Peloton for ₹58,500 second-hand. Worth it?"

Step 1 — Recall commitment: "You committed last month to putting ₹25,000 into your travel fund
  (cmt_XYZ, active)."

Step 2 — Fetch fresh: [get_account_balance → checking ₹87,500]
  [get_upcoming_bills → SIP ₹9,000, Internet+Mobile ₹4,200, Credit Card ₹6,500]

Step 3 — Math: "Bills due ₹19,700. Travel-fund transfer ₹25,000. Leaves ₹87,500 − ₹19,700 − ₹25,000 =
  ₹42,800 before variable spend. Peloton at ₹58,500 puts you ₹15,700 short before food/fuel."

Step 4 — Alternative: "Hold until next month after the SIP and transfer clear — you'd start fresh on
  salary day and could earmark ₹58,500 without touching the travel fund."

Step 5 — [set_reminder("YYYY-MM-DD", "Peloton decision deadline")]
  "I've set a 48-hour reminder so the window doesn't drift. The math is yours to weigh."
</example>
</discretionary_purchase_frame>

<session_briefing>
{briefing}
</session_briefing>

Knowledge about {name}:

{memory_block}

Before any tool call, state in one short sentence what you're about to do and why.
"""


# ---------- tool handlers ----------

def _h_get_recent_transactions(args: dict, ctx: dict) -> Any:
    """Filter by date in Python (NOT string subtraction) and auto-append category totals."""
    days = int(args["days"])
    today = date.fromisoformat(ctx["current_date"])
    since = today - timedelta(days=days)
    raw = user_tools.get_recent_transactions(days)
    filtered = [t for t in raw if date.fromisoformat(t["date"]) >= since]

    totals: dict[str, dict[str, Any]] = {}
    for t in filtered:
        if t["amount"] >= 0:
            continue  # credits don't count as spending
        cat = t["category"]
        entry = totals.setdefault(cat, {"total_inr": 0, "count": 0, "merchants": set()})
        entry["total_inr"] += -t["amount"]
        entry["count"] += 1
        entry["merchants"].add(t["merchant"])
    for cat in totals:
        totals[cat]["merchants"] = sorted(totals[cat]["merchants"])

    return {
        "transactions": filtered,
        "category_totals": totals,
        "days_window": days,
        "since_date": since.isoformat(),
        "as_of": today.isoformat(),
    }


def _h_get_account_balance(args: dict, ctx: dict) -> Any:
    return user_tools.get_account_balance()


def _h_get_upcoming_bills(args: dict, ctx: dict) -> Any:
    return user_tools.get_upcoming_bills(int(args.get("days", 30)))


def _h_set_reminder(args: dict, ctx: dict) -> Any:
    result = user_tools.set_reminder(args["date"], args["content"])
    ctx["memory"].record_reminder(result, ctx["session_id"], ctx["current_date"])
    ctx["memory"].save(MEMORY_FILE)
    return result


def _h_remember(args: dict, ctx: dict) -> Any:
    result = ctx["memory"].remember(
        kind=args["kind"],
        summary=args["summary"],
        details=args["details"],
        confidence=args["confidence"],
        session_id=ctx["session_id"],
        date=ctx["current_date"],
    )
    if result.get("status") == "stored":
        ctx["memory"].save(MEMORY_FILE)
    return result


TOOL_HANDLERS: dict[str, Callable[[dict, dict], Any]] = {
    "get_recent_transactions": _h_get_recent_transactions,
    "get_account_balance":     _h_get_account_balance,
    "get_upcoming_bills":      _h_get_upcoming_bills,
    "set_reminder":            _h_set_reminder,
    "remember":                _h_remember,
}

# Module-load guard: TOOL_HANDLERS and TOOLS must agree on names, or dispatch silently 404s.
# Using `raise` not `assert` so the check survives `python -O` (which strips asserts).
_missing = {t["name"] for t in TOOLS} ^ TOOL_HANDLERS.keys()
if _missing:
    raise RuntimeError(f"TOOLS / TOOL_HANDLERS name mismatch: {_missing}")


def dispatch(name: str, args: dict, ctx: dict) -> tuple[str, bool]:
    """Returns (json_content_string, is_error)."""
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return json.dumps({"error": f"unknown tool: {name}"}, ensure_ascii=False), True
    try:
        result = handler(args, ctx)
        return json.dumps(result, default=str, ensure_ascii=False), False
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False), True


# ---------- session briefing ----------

def generate_briefing(memory: Memory, current_date: str) -> str:
    """Pre-turn briefing built from memory state. Pure function — no I/O, no LLM.
    Written to the transcript as [SESSION BRIEFING] AND injected into the system
    prompt so the agent surfaces what it remembers before the user types."""
    active = memory.active_commitments()
    pending = memory.pending_reminders()
    patterns = memory.data["acknowledged_patterns"]
    sessions = memory.data["session_log"]
    if not (active or pending or patterns or sessions):
        return f"Session brief ({current_date}): first conversation with this user. No prior commitments, patterns, or reminders."
    parts = [f"Session brief ({current_date}):"]
    if active:
        parts.append(f"{len(active)} active commitment(s): " + "; ".join(c["summary"] for c in active) + ".")
    if pending:
        parts.append(f"{len(pending)} pending reminder(s): " + "; ".join(f"{r['date']} — {r['content']}" for r in pending) + ".")
    if patterns:
        parts.append(f"{len(patterns)} acknowledged pattern(s): " + "; ".join(p["summary"] for p in patterns) + ".")
    if sessions:
        last = sessions[-1]
        parts.append(f"Last session {last['date']}: {last['summary']}.")
    return " ".join(parts)


# ---------- transcript logger (tee) ----------

class Tee:
    def __init__(self, path: Path):
        self.f = open(path, "w", encoding="utf-8")
    def __call__(self, s: str) -> None:
        self.f.write(s)
        self.f.flush()
        print(s, end="")
    def close(self) -> None:
        self.f.close()


def _render_block(block) -> str:
    if block.type == "text":
        return f"\n[assistant text]\n{block.text}\n"
    if block.type == "tool_use":
        return f"\n[tool_use] {block.name}({json.dumps(block.input, ensure_ascii=False, default=str)})\n"
    return f"\n[block:{block.type}] (not rendered)\n"


# ---------- agent loop ----------

def _turn(client, system_text: str, messages: list, ctx: dict, w: Callable[[str], None]) -> None:
    """Run one user-turn through the loop. Accumulates token usage into ctx['run_usage'].
    Raises CostCapExceeded if the run total goes over MAX_USD_PER_RUN."""
    run_usage = ctx["run_usage"]
    for iteration in range(MAX_LOOP_ITERATIONS):
        t0 = time.perf_counter()
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            temperature=0,
            system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
            tools=TOOLS,
            messages=messages,
        )
        latency_ms = (time.perf_counter() - t0) * 1000.0
        ctx["latencies_ms"].append(latency_ms)
        u = response.usage
        cache_r = getattr(u, "cache_read_input_tokens", 0) or 0
        run_usage["input"]          += u.input_tokens
        run_usage["output"]         += u.output_tokens
        run_usage["cache_read"]     += cache_r
        run_usage["cache_creation"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        run_cost = _cost_usd(run_usage)
        messages.append({"role": "assistant", "content": [b.model_dump() for b in response.content]})
        w(f"\n--- iter {iteration+1} stop={response.stop_reason} usage(in={u.input_tokens} out={u.output_tokens} cache_r={cache_r}) latency_ms={latency_ms:.0f} cumulative_cost_usd={run_cost:.4f} ---")
        if run_cost > MAX_USD_PER_RUN:
            w(f"\n[COST CAP EXCEEDED] run total ${run_cost:.4f} > ${MAX_USD_PER_RUN:.2f}. Aborting session gracefully.\n")
            raise CostCapExceeded(f"run cost ${run_cost:.4f} exceeded cap ${MAX_USD_PER_RUN:.2f}")
        for block in response.content:
            w(_render_block(block))
        if response.stop_reason == "end_turn":
            return
        if response.stop_reason not in ("tool_use", "pause_turn"):
            w(f"\n[unexpected stop_reason: {response.stop_reason}]\n")
            return
        # pause_turn is the server's internal-iteration cap; tool_use blocks are still present and must be dispatched.
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            content_str, is_error = dispatch(block.name, block.input, ctx)
            w(f"[tool_result] {block.name} -> {content_str}\n")
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": content_str,
                **({"is_error": True} if is_error else {}),
            })
        messages.append({"role": "user", "content": tool_results})
    w(f"\n[LOOP GUARD: hit MAX_LOOP_ITERATIONS={MAX_LOOP_ITERATIONS}]\n")
    print(f"WARNING: loop guard hit on session {ctx['session_id']}", file=sys.stderr)


def _build_system_text(memory: Memory, current_date: str, weekday: str, briefing: str) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        name=memory.first_name(),
        age=memory.data["user_profile"]["age"],
        city=memory.data["user_profile"]["city"],
        weekday=weekday,
        current_date=current_date,
        briefing=briefing,
        memory_block=memory.to_prompt_block(),
    )


def _build_session_ctx(session_id: int) -> dict:
    """Shared setup for both scripted and interactive runners. Returns a ctx dict
    carrying every piece of session state the loop or its handlers may need."""
    current_date, weekday = SESSION_META[session_id]
    memory = Memory.load(MEMORY_FILE)
    ctx = {
        "memory": memory, "session_id": session_id, "current_date": current_date,
        "weekday": weekday, "pre_counts": memory.counts(), "run_usage": _zero_usage(),
        "latencies_ms": [],
    }
    ctx["briefing"] = generate_briefing(memory, current_date)
    ctx["system_text"] = _build_system_text(memory, current_date, weekday, ctx["briefing"])
    return ctx


def _finalize_session(ctx: dict) -> float:
    """Close the session, persist memory, and return the final cost in USD."""
    ctx["memory"].close_session(ctx["session_id"], ctx["current_date"], ctx["pre_counts"])
    ctx["memory"].save(MEMORY_FILE)
    return _cost_usd(ctx["run_usage"])


def _latency_summary(latencies_ms: list[float]) -> dict[str, float]:
    """Per-turn API-call latency stats. Empty list → zeros (no calls made)."""
    if not latencies_ms:
        return {"calls": 0, "median_ms": 0.0, "p95_ms": 0.0, "total_ms": 0.0}
    sorted_l = sorted(latencies_ms)
    p95_idx = max(0, int(round(0.95 * len(sorted_l))) - 1)
    return {
        "calls": len(latencies_ms),
        "median_ms": round(median(latencies_ms), 1),
        "p95_ms": round(sorted_l[p95_idx], 1),
        "total_ms": round(sum(latencies_ms), 1),
    }


def run_scripted_session(session_id: int) -> None:
    ctx = _build_session_ctx(session_id)
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    w = Tee(TRANSCRIPT_DIR / f"session{session_id}.log")
    client = anthropic.Anthropic()
    messages: list = []

    try:
        w(f"{'='*72}\nSession {session_id} — {ctx['weekday']}, {ctx['current_date']}\n{'='*72}\n")
        w(f"\n[SESSION BRIEFING]\n{ctx['briefing']}\n")
        w(f"\n[MEMORY AT SESSION START]\n{ctx['memory'].to_prompt_block()}\n")

        aborted_reason: str | None = None
        try:
            for idx, user_text in enumerate(SESSION_TURNS[session_id], start=1):
                w(f"\n\n{'-'*72}\nUSER turn {idx}\n{'-'*72}\n{user_text}\n")
                messages.append({"role": "user", "content": user_text})
                _turn(client, ctx["system_text"], messages, ctx, w)
        except CostCapExceeded as e:
            aborted_reason = str(e)
            print(f"WARNING: {e}", file=sys.stderr)

        final_cost = _finalize_session(ctx)
        lat = _latency_summary(ctx["latencies_ms"])
        w(f"\n\n[MEMORY AT SESSION END]\n{ctx['memory'].to_prompt_block()}\n")
        w(f"\n[TOKEN USAGE TOTAL] {json.dumps(ctx['run_usage'])}  cost_usd={final_cost:.4f}\n")
        w(f"[LATENCY] api_calls={lat['calls']} median_ms={lat['median_ms']} p95_ms={lat['p95_ms']} total_ms={lat['total_ms']}\n")
        if aborted_reason:
            w(f"\n[ABORTED] {aborted_reason}\n")
        print(f"\nWrote {TRANSCRIPT_DIR / f'session{session_id}.log'} and {MEMORY_FILE}. "
              f"Cost: ${final_cost:.4f}  p95_latency: {lat['p95_ms']:.0f}ms")
    finally:
        w.close()


def run_interactive_session(session_id: int) -> None:
    ctx = _build_session_ctx(session_id)
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    log_path = TRANSCRIPT_DIR / f"interactive_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    w = Tee(log_path)
    client = anthropic.Anthropic()
    messages: list = []

    try:
        w(f"{'='*72}\nInteractive session {session_id} — {ctx['weekday']}, {ctx['current_date']}\n{'='*72}\n")
        w(f"\n[SESSION BRIEFING]\n{ctx['briefing']}\n")
        w(f"\n[MEMORY AT SESSION START]\n{ctx['memory'].to_prompt_block()}\n")
        print("\nType your message. Empty line to exit.\n")

        try:
            while True:
                try:
                    line = input("> ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if not line:
                    break
                w(f"\n\n{'-'*72}\nUSER (interactive)\n{'-'*72}\n{line}\n")
                messages.append({"role": "user", "content": line})
                _turn(client, ctx["system_text"], messages, ctx, w)
                print()
        except CostCapExceeded as e:
            print(f"\nWARNING: {e}", file=sys.stderr)

        final_cost = _finalize_session(ctx)
        lat = _latency_summary(ctx["latencies_ms"])
        w(f"\n\n[MEMORY AT SESSION END]\n{ctx['memory'].to_prompt_block()}\n")
        w(f"\n[TOKEN USAGE TOTAL] {json.dumps(ctx['run_usage'])}  cost_usd={final_cost:.4f}\n")
        w(f"[LATENCY] api_calls={lat['calls']} median_ms={lat['median_ms']} p95_ms={lat['p95_ms']} total_ms={lat['total_ms']}\n")
        print(f"\nWrote {log_path}. Cost: ${final_cost:.4f}  p95_latency: {lat['p95_ms']:.0f}ms")
    finally:
        w.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", type=int, choices=(1, 2), default=None,
                        help="Override tools.CURRENT_SESSION (e.g. --session 2). "
                             "Without this flag, falls back to tools.CURRENT_SESSION.")
    parser.add_argument("--interactive", action="store_true",
                        help="Stdin REPL instead of scripted messages. Tees to transcripts/interactive_<ts>.log.")
    args = parser.parse_args()
    session_id = args.session if args.session is not None else user_tools.CURRENT_SESSION
    if session_id not in SESSION_META:
        raise SystemExit(f"session_id must be 1 or 2, got {session_id}")
    # Sync the tools module's session switch with our chosen session_id. tools.get_account_balance
    # and tools.get_recent_transactions branch on tools.CURRENT_SESSION at call time, so the override
    # has to land BEFORE any handler runs — otherwise --session 2 reads S1 mock data.
    user_tools.CURRENT_SESSION = session_id
    if args.interactive:
        run_interactive_session(session_id)
    else:
        run_scripted_session(session_id)


if __name__ == "__main__":
    main()
