# SHRED2CHART — Project Index

This file is the local entry point for project architecture, status, decisions, and agent handoffs. The former 577-line monolith has been split to match the Notion project structure and to keep each source readable by tools with response-size limits.

The pre-split working copy is preserved at [legacy-gameplan-monolith.md](docs/gameplan/legacy-gameplan-monolith.md). It is historical only and must not be updated.

## Authoritative project documents

1. [Architecture & Design](docs/gameplan/architecture.md) — project goal and pipeline stages.
2. [Milestones](docs/gameplan/milestones.md) — implemented, verified, partially complete, and blocked work.
3. [Current State](docs/gameplan/current-state.md) — active session summary and newest entries.
4. [Decision Log](docs/gameplan/decision-log.md) — decisions and reversals with rationale.
5. [Open Questions](docs/gameplan/open-questions.md) — active, resolved, obsolete, and eliminated questions.

Supporting records:

- [Current State Archive](docs/gameplan/current-state-archive.md) — older session history.
- [Agent Protocol](AGENTS.md) — mandatory read/update workflow for coding agents.
- [Agent Handoff Log mirror](docs/gameplan/handoff-log.md) — local pointer and recent structural handoffs.
- [HANDOFF.md](HANDOFF.md) — end-user installation and conversion guide; it is not the agent execution log.

## Source-of-truth order

Read the index and the five authoritative documents above, then the latest handoffs, then inspect code and tests. The corresponding Notion pages are the shared multi-agent record; this repository is the final authority for what is actually implemented. A discrepancy between the two is a documentation bug and must be recorded and reconciled.

## Quick status

GP7/8 `.gp` files use direct GPIF parsing; legacy `.gp3/.gp4/.gp5` use PyGuitarPro; GP6 `.gpx` compatibility remains. M4 mapping and M5 CLI work are substantially implemented but retain human-verification and real-material quality gates. See [Current State](docs/gameplan/current-state.md) for the newest repository warnings and next steps.

## Update rule

Every work session must:

1. Read [AGENTS.md](AGENTS.md) and the documents above before changing code.
2. Append the session to [Current State](docs/gameplan/current-state.md).
3. Create/update the canonical Notion Handoff Log and refresh the [local mirror](docs/gameplan/handoff-log.md).
4. Update [Decision Log](docs/gameplan/decision-log.md) when a decision changes or is reversed.
5. Keep local and Notion copies synchronized in the same session.
