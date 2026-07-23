# Agent Protocol

> Local mirror of the [authoritative Notion Agent Protocol](https://app.notion.com/p/3a4b82db13b781d59d08d3cc7e299f9e). Keep both copies synchronized.

Read this before touching SHRED2CHART. Applies to every agent — Claude Code, Copilot, ChatGPT, Claude (chat), whatever's next.
## Source of truth, in order
The Game Plan is no longer a single page — it's an index page plus five sub-pages (Architecture, Milestones, Current State, Decision Log, Open Questions). Read in this order:
1. **Game Plan index** — one-paragraph status + links to everything below. Start here, not at Architecture.
2. **Architecture & Design** (sub-page) — goal, non-goals, pipeline stages. Static reference; skim unless you need stage-level detail.
3. **Milestones** (sub-page) — M0-M5 status. Check what's actually done vs. awaiting human verification before assuming a stage is finished.
4. **Current State** (sub-page) — chronological session log. Read at least the last few entries (and the linked Archive if the active page says to check it) to know what just happened.
5. **Decision Log** (sub-page) — architectural decisions with rationale. Check this before re-deciding something already settled.
6. **Open Questions** (sub-page) — unresolved/quality questions. Check before treating something as a fresh discovery.
7. **Agent Handoff Log** (this database's sibling) — chronological execution history, session-by-session. Read the last 3-5 entries, sorted by Timestamp descending, before starting anything.
8. **GitHub** — actual code. The final authority on what's actually implemented.
All of 1-6 above are, collectively, authoritative — if it's not written in one of those pages, it didn't happen. If the Game Plan pages and the Handoff Log ever disagree, the Game Plan wins — but that disagreement itself is a bug, flag it and fix the Log entry.
## Before starting work
**Mandatory preflight: do not inspect, edit, run, or propose code until every item below is complete.** An agent may skim static sections, but may not skip any source entirely.
1. Read the **Game Plan index** and open every linked authoritative sub-page.
2. Read **Architecture & Design** closely enough to identify which pipeline stage the task touches, its invariants, and any superseded design notes.
3. Read **Milestones** and record whether the relevant milestone is implemented, verified, partially complete, or blocked on human validation.
4. Read **Current State** through its newest entry. If the page links an archive or is truncated by the tool, retrieve the missing recent material before proceeding.
5. Read the full **Decision Log**, not only entries whose titles appear related. Confirm that the proposed work does not reverse or duplicate a settled decision.
6. Read the full **Open Questions** page. Distinguish active questions from resolved, obsolete, or eliminated risks.
7. Read the latest **3–5 Agent Handoff Log entries**, sorted by Timestamp descending. Check **Next Agent**, **Blockers**, **Dependencies**, **Validation**, and **Status**.
8. Inspect the relevant **GitHub code and tests** only after steps 1–7, then compare implementation against the documentation. Treat discrepancies as documentation or code bugs to flag explicitly.
9. Before making changes, write down the task's current state, applicable decisions, blockers, and intended validation. Check whether the work is already **In progress** or **Done**. Do not silently re-attempt blocked or completed work.
10. If architecture or project state has changed since the documentation was updated, synchronize the authoritative page before—or in the same change as—code written against the new state.
**Preflight completion rule:** the session's final handoff must state which pages and handoff entries were read. If any required source could not be retrieved in full, list that as a blocker or limitation before starting implementation rather than assuming its contents.
## While working
- Never silently reverse a logged decision. If you're overturning one, log the reversal with rationale in the Game Plan's Decision Log (see the 2026-07-17 Route A/B entry for the pattern).
- Milestone order is strict: M0 gates everything. Don't build on top of an unverified milestone without noting that it's unverified.
- Every milestone's human verification step is mandatory before checkoff — an agent can't check its own homework here (GUI spot-checks, Clone Hero playtests, etc. need the human).
- List blockers explicitly. "Ran out of time" is not a blocker. "Needs a real .gpx file, none exists in this repo" is a blocker.
## Before ending a session
Create a Handoff Log entry. Every entry's **Summary/Decisions Made** field (or an attached comment) follows this exact structure — same five questions, every time, so parsing a session doesn't require interpreting prose style:
1. **What was completed?**
2. **What is the current project state?**
3. **What remains?**
4. **What should the next agent do first?**
5. **What files or pages should they read?**
Also fill in the structured fields: Agent, Timestamp, Session ID, Parent Session (if continuing prior work), Branch, Commit/PR, Components touched, Priority, Inputs, Outputs, Validation (tests run + pass/fail), Blockers, Confidence (1-5, optional), Next Agent, Status.
If you changed architecture, scope, or a prior decision: update the Game Plan's **Current State** and **Decision Log** sections too. The Handoff Log entry alone is not enough — it's the audit trail, not the spec.
## Quick reference

| Thing | Local source |
| --- | --- |
| Architecture, milestones, current state, decisions, open questions | [SHRED2CHART_GAMEPLAN.md](SHRED2CHART_GAMEPLAN.md) and its linked files |
| Session execution history | [Local handoff mirror](docs/gameplan/handoff-log.md) and the canonical Notion Handoff Log |
| Code | This Git repository |
| Generated songs/charts/test fixtures | Local artifacts; do not add them to project-state documents |
