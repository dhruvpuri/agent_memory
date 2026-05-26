# AI Engineer Assignment

## Context

We're hiring an engineer to help build an AI finance companion — a chat-first agent that understands a user's finances, remembers them across sessions, and proactively helps them make better money decisions.

This assignment is a thin slice of that. You'll build an agent that holds two conversations with the same user, three days apart, and demonstrates it actually learned something from the first conversation.

Scope is intentionally tight. We expect a strong submission to take a focused weekend.

---

## What you build

- The agent loop
- The memory layer (must persist to disk between sessions — file, SQLite, whatever)
- The prompts
- Anything else needed to make the two sessions work

## What we provide (in this folder)

- `tools.py` — four tool function stubs with built-in fake data. You call them, you don't implement them.
- `sessions.md` — the exact user messages for both sessions, so submissions are comparable.
- A user profile, in this README below.

---

## The scenario

**Session 1 (Monday, Nov 3).** The user has just received their monthly salary and wants help planning savings. There are four user turns. Your agent responds to each. The user commits to a savings plan and asks for a reminder.

**Session 2 (Thursday, Nov 6, three days later).** The user comes back with a one-line opener about wanting to make a purchase. Your agent should — without being told to — demonstrate four things:

1. It remembers the savings plan from Monday (**memory**).
2. It connects the new question to that plan on its own (**judgment**).
3. It uses tools to check things that may have changed since Monday — balance, upcoming bills — rather than quoting stale numbers from memory (**tool vs. memory discipline**).
4. It takes at least one action via `set_reminder` where appropriate (**tool calling**).

---

## User profile

```python
USER_PROFILE = {
    "name": "Priya Sharma",
    "age": 28,
    "city": "Bangalore",
    "monthly_income_inr": 120000,  # post-tax, credited on the 1st
    "stated_goal": "Save ₹15 lakh in 2 years for a house down payment in Bangalore",
}
```

---

## Constraints

- **No agent frameworks.** No LangChain, LlamaIndex, CrewAI, etc. Write the loop yourself. We want to see your architecture.
- **LLM calls only where judgment is needed.** If you're using an LLM to do arithmetic, parse a date, or sum a column, that's a flag.
- **Memory must persist on disk** between Session 1 and Session 2. In-process state doesn't count.
- **Aim for under 300 lines of code total.** Smaller is better if it works.
- **AI assistance is encouraged.** We expect you to use Claude, Cursor, Codex, etc. The writeup will ask about it directly.

---

## Deliverables

1. **Code** — public or shared private repo.
2. **Full transcripts** of both sessions, with tool calls and memory reads/writes visible in the logs.
3. **A 10-minute Loom** walking through your code. Required structure:
   - Show the memory layer and what's stored
   - Walk through one tool-call decision in your loop
   - Pull up one prompt and explain why it's written the way it is

   One take preferred — don't over-produce it.

4. **A one-page writeup** answering exactly these four questions:
   - **Memory:** What did you store after Session 1, and what did you deliberately *not* store? Why?
   - **Tools vs. LLM:** Name one decision in your code you gave to the LLM, and one you kept as code. Why each?
   - **AI usage:** Which parts did you generate with AI? Give one specific example where the AI suggested something and you rejected it — what did it suggest, and why was it wrong?
   - **One week more:** If you had another week, what one thing would you redesign, and why?

---

## How to run

```
your-agent/
├── tools.py          # provided, do not modify
├── sessions.md       # provided, do not modify
├── agent.py          # yours
├── memory.json       # or .db, or whatever — created at runtime
└── ...
```

To run Session 1, leave `CURRENT_SESSION = 1` in `tools.py`. After Session 1 finishes, change it to `2` and run Session 2. Your memory layer should persist between the two runs.

---

## Logistics

**Deadline: 10 PM IST on the Sunday after you receive this assignment.**

Submit by emailing **hello@goreach.finance** with the repo link, Loom link, and writeup.

Questions before you start: email us. Questions during: prefer making a reasonable assumption and noting it in the writeup.
