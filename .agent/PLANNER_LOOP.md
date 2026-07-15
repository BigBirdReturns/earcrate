# Planner loop protocol

Purpose: stop the owner from being the manual glue between "Claude hit a decision
that needs foresight/expert judgment" and "go ask another LLM." The repo holds the
long view; hands (Claude Code) read it, execute, and only round-trip to the
planner (ChatGPT, via the Browser pane) at genuine judgment points — then write
what happened back down so the next iteration (or the next session, cold) doesn't
re-derive it.

This is a protocol, not a script. No code runs this automatically; hands follow
these steps by hand each time the loop fires.

## When to invoke the planner

Only at a genuine fork that needs foresight, not at things resolvable by reading
the repo. Concretely: architectural tradeoffs with no clear winner in
`EARCRATE_REBUILD_PLAN_v3.md`/`AGENTS.md`, sequencing calls across multiple
`docs/AGENT_HANDOFF.json` findings, or "which of these approaches fits the
product's direction" questions. Do NOT invoke it for anything a grep, a test run,
or `PRODUCT.md`/`AGENTS.md` already answers — that's just avoiding work.

## Step 1 — Read the long view
- `.agent/INDEX.md` for the map.
- `.agent/journal/<latest>.md` tail — what the last iteration decided and why.
- Whatever `INDEX.md` points to for the specific area in question (e.g. the
  relevant `qa_findings[]` entries in `docs/AGENT_HANDOFF.json` before touching
  anything in that surface).

## Step 2 — Ask the planner
Open the Browser pane (chatgpt.com, logged-in session). Post ONE self-contained
message containing:
- The concrete current state (not a summary — paste the actual finding/error/diff)
- The specific fork or decision, with the options already narrowed if possible
- Any binding constraints from `AGENTS.md` / `EARCRATE_REBUILD_PLAN_v3.md` that
  apply, quoted, so the planner doesn't recommend something already forbidden
- What "done" looks like for this step

Do not ask ChatGPT to replan the whole project. One decision per round-trip.

## Step 3 — Act
Execute the planner's answer. If it conflicts with a nonnegotiable rule in
`AGENTS.md`, do NOT follow it — that's a stop-and-flag-to-owner case, not a
silent override.

## Step 4 — Verify
Same bar as everywhere else in this repo: exercised through the real UI/API/test,
not "imported successfully." For anything render-related, the three mandatory
gates in `RECONSTRUCTION_CONTRACT.md` apply if the reconstruction lab is in scope.

## Step 5 — Log
Append an entry to today's journal file (`.agent/journal/YYYY-MM-DD.md`, create if
absent) in this shape:

```
## <time> — <one-line topic>
**State:** <what was true going in>
**Asked planner:** <the actual question/constraints sent>
**Planner said:** <the actual answer, verbatim or tightly paraphrased>
**Did:** <what was executed>
**Verified:** <how, and result>
**Follow-up:** <anything left open, or "none">
```

If the decision changes durable project state (not just "did a task"), also
update `.agent/INDEX.md` or the relevant doc it points to — the journal is the
narrative, the index/docs are the current truth. Don't let them drift apart.

## Non-goals
- Not a replacement for `docs/AGENT_HANDOFF.json` (that's the QA ledger) or
  `MILESTONES.md` (that's the roadmap) — this protocol is the connective tissue
  between "read state," "get a judgment call," and "log what happened," nothing more.
- Not an autonomous/unattended loop. The owner is still the one who decides when
  to fire an iteration; this just removes the owner from being the copy-paste
  wire between the two models.
