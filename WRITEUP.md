# Writeup

## Memory: what I stored after Session 1, and what I deliberately did not

Memory holds intent. Tools hold facts. After Session 1, `memory.json` contains:

- One commitment (`cmt_001`): a ₹30,000 monthly transfer to the house fund, next due 2025-11-25. Tagged `confidence: stated_by_user`, source session 1, status active.
- One acknowledged pattern (`pat_001`): Priya accepted that her October food-delivery spend was higher than expected (₹12,890 across 10 orders). The system prompt has an explicit self-trigger for user acknowledgments like "that's worse than I thought," which is why it fired.
- One reminder for Nov 25 (transfer the ₹30,000), persisted by the `set_reminder` dispatch wrapper.
- A session log entry derived from counting what was actually written. Reads "1 new commitment; 1 new pattern; 1 new reminder."
- The user profile (name, age, city, income, salary date, stated goal). Present at session start, unchanged.

I deliberately do not store balances, transaction lists, upcoming bills, the model's chain of thought, full transcripts, or any opinion the user did not accept. Two reasons.

First, anything that can change between sessions has to be fetched live or it becomes a stale-data bug. A finance agent that quotes Monday's balance on Thursday is broken in a particularly embarrassing way. `eval.py` asserts this on the Session 2 transcript: `get_account_balance` was called, and the response quotes the fresh ₹99,820 instead of the stale ₹128,000 from Monday.

Second, every entry carries `confidence: stated_by_user | inferred_by_agent` as provenance. When the agent reads `cmt_001 (stated_by_user)` it knows that's the user's word. Without that tag, the agent's own projections would compound across sessions and quietly become "facts."

One deliberate limitation: the memory block is injected at session start, so within-session writes reach the model through `tool_result` history rather than a re-rendered prompt. At four turns this never bites; the `retrieve_memories` redesign in the last section removes it entirely.

On size: the code runs ~950 LoC against the spec's 300 soft target. About 130 lines are removable conveniences (cost cap, tee logger, `--interactive` REPL, `.env.local` loader, latency instrumentation, `--session` CLI override); the architectural core lands around 330 lines combined with `memory.py`.

## Tools vs LLM: one decision I gave to the LLM, one I kept in code

**LLM**: deciding whether a user utterance is a commitment worth persisting. "I want to put aside ₹30,000" is a promise. "I wonder if I should save more" is musing. No regex catches the difference; it's pure judgment. The model decides whether to call `remember(kind="commitment", confidence="stated_by_user")`. If it gets the confidence wrong, provenance makes the error visible later.

**Code**: arithmetic over the transaction list. The `_h_get_recent_transactions` dispatch wrapper filters by date (real `datetime.date` comparison, not string subtraction) and auto-appends `category_totals`. Python computes the sums; the model reads them. The spec explicitly flags "LLM summing a column" as a problem. My version of that rule: the LLM never gets the chance to sum, because Python has done it by the time the tool result lands. Single-scalar arithmetic (one subtraction for an affordability check) stays with the model. Cheap, visible in the trace.

## AI usage: what I generated with Claude, and one suggestion I rejected

I used Claude Code (Sonnet 4.6 and Opus 4.7) for architecture scaffolding, system-prompt drafting, an adversarial review pass before writing any code, and a sanity audit on the loop before spending API tokens. Specialist agents caught real blockers I would have shipped otherwise: the `memory.save()` race on `set_reminder`, the date-subtraction bug in the transaction wrapper (`txn["date"] - days` does not work on strings), and a prompt-cache token-threshold miscalculation. The final architecture is mine; Claude is a multiplier.

One concrete rejection. The prompt-engineer subagent's first draft of the `<discretionary_purchase_frame>` worked example used the literal Session 2 numbers: a ₹80,000 MacBook against a ₹99,820 checking balance and the exact bills `get_upcoming_bills` returns on Session 2. I rejected because those numbers would also be the Session 2 ground truth, so Sonnet could pattern-match the example and skip the real `get_account_balance` call. I rewrote with an anonymous user, a ₹58,500 Peloton, and an ₹87,500 balance, all deliberately mismatched. The Session 2 transcript confirms the fix: the agent fetched the real ₹99,820 from the tool rather than echoing the example.

## One week more: the one thing I would redesign

I would build an **agent-invoked memory retrieval tool** — `retrieve_memories(query, topics, max_age_days)` — and stop injecting the full memory blob every turn. Cleo's published memory architecture (July 2025), Mem0's *State of Agent Memory 2026*, and Anthropic's Managed Agents memory feature all converged on this shape. Today I inject the whole blob at session start. That's fine at our scale (one commitment, three reminders), but it costs tokens and latency on every turn regardless of relevance.

For a goreach demographic on Indian mobile networks, p95 latency and per-session token cost are real product constraints. Concretely, this submission burned ~$0.11 across both scripted sessions over 10 API calls, with median per-turn latency ~5s and p95 ~13s on Sonnet 4.6. Prompt caching is already doing work — after turn 1, cached input tokens land at $0.30/M instead of $3.00/M, a 10× saving on the system block — but the injected memory grows the input linearly with state. Once memory holds 50+ entries the per-turn balloon makes p95 a real product problem, not an instrumentation curiosity. Letting the model decide when to query memory, and letting the harness write asynchronously after the response ships, closes both gaps.
