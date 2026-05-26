# Sessions

Run your agent against these messages in order, exactly as written.

Your agent's responses are up to you. Tool calls and memory reads/writes should be visible in your logs.

---

## Session 1 — Monday, Nov 3, 2025

> Set `CURRENT_SESSION = 1` in `tools.py`.

**User turn 1**
> I just got my salary credited. Help me figure out how much I can realistically save this month.

**User turn 2**
> I feel like I'm spending too much on food delivery. How much did I actually spend on it last month?

**User turn 3**
> Okay that's worse than I thought. Let's say I want to cut that in half AND put aside ₹30,000 for my house fund this month — is that realistic given my upcoming bills?

**User turn 4**
> Got it. Remind me to actually transfer the ₹30,000 to my house fund on the 25th.

---

## Session 2 — Thursday, Nov 6, 2025

> Set `CURRENT_SESSION = 2` in `tools.py`.
> Your memory layer from Session 1 should still be on disk.

**User turn 1**
> Hey, my colleague is selling his MacBook for ₹80,000, barely used. I've been wanting to upgrade. Should I buy it?

---

A good response to the Session 2 message is **not** a flat "yes" or "no". It's a response that ties the new question to what your agent already knows about this user, checks the things worth checking right now, and helps them decide.
