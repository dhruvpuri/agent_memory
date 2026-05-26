# Conversational finance companion

A chat-first finance agent that holds two conversations with the same user three days apart and carries what it learned from the first into the second. Hand-rolled agent loop on the Anthropic SDK — no agent framework.

## Run

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-...

# Session 1 (Monday), then Session 2 (Thursday, 3 days later)
python agent.py --session 1
python agent.py --session 2

# Checks
python validate_memory.py
python eval.py

# Free-form REPL against the same memory + tools (off-script chat)
python agent.py --interactive --session 2
```

`--session N` overrides `tools.CURRENT_SESSION` for the run; the runner mutates the tools module's switch so mock tools return the matching session's data. Omit `--session` to fall back to whatever `tools.py` has set.

Sessions write to `transcripts/`; memory persists to `memory.json` between runs. A `.env.local` with `ANTHROPIC_API_KEY=...` is loaded automatically if present.

## Layout

| File | Role |
|------|------|
| `agent.py` | Agent loop, tool schemas, named dispatch, system prompt, session briefing |
| `memory.py` | On-disk memory with provenance tags and atomic writes |
| `validate_memory.py` | Schema sanity test |
| `eval.py` | Six behavioral assertions on the Session 2 transcript (briefing fires, tools called, fresh balance quoted, no stale recall, cross-session memory continuity) |
| `WRITEUP.md` | Design notes: memory model, tools-vs-LLM split, AI usage, what I'd redesign |
| `requirements.txt` | One line: `anthropic>=0.104` |
| `tools.py`, `sessions.md` | Provided fixtures — mock tools and the scripted user messages |
| `ASSIGNMENT.md` | The original brief |

## How it works

Memory holds **intent** — commitments, acknowledged patterns, reminders — each tagged with provenance: whether the fact was stated by the user or inferred by the agent. Tools hold **facts** — live balance, bills, transactions — always fetched fresh, never recalled. At the start of Session 2 the agent surfaces a briefing from memory before the user speaks, then ties the new question to what it already knows. `WRITEUP.md` has the full reasoning.
