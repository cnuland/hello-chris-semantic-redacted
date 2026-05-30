# Agent Pipeline: Privacy-Preserving Semantic Routing

## Pipeline Architecture

Four agents execute sequentially, each reading all previous handoffs and producing their own. Any agent can escalate back to a previous agent with structured feedback.

```
  ┌───────────┐     ┌──────────────┐     ┌──────────┐     ┌───────────┐
  │  PLANNER  │────▶│  PROGRAMMER  │────▶│  TESTER  │────▶│  REVIEWER │
  │  (PM)     │     │  (Dev)       │     │  (QA)    │     │  (Eval)   │
  └───────────┘     └──────────────┘     └──────────┘     └───────────┘
       ▲                  ▲                  ▲                  │
       │                  │                  │                  │
       └──────────────────┴──────────────────┴──────────────────┘
                         Escalation / Clarification
```

## Execution Protocol

### Before Starting (All Agents)

1. Read `CLAUDE.md` for project constraints
2. Read your `agents/<role>/AGENT.md` for role definition
3. Read your `agents/<role>/skills.md` for capability inventory
4. Read all previous agents' `handoff.md` files (if any exist)
5. Check `pipeline-state.md` for current status

### Phase 1: Planner

**Input:** `research.md`, `overview.md`, user requirements
**Output:** `agents/planner/handoff.md`

The planner decomposes the project into implementable work units:

1. Read all architecture and design docs in `docs/`
2. Create a task breakdown with explicit acceptance criteria per task
3. Identify dependencies between tasks
4. Create a risk register (what could go wrong, mitigations)
5. Define the deployment sequence (what gets deployed first)
6. Write `agents/planner/handoff.md` with the complete plan
7. Update `pipeline-state.md`

**Escalation triggers:**
- Requirements are ambiguous → Escalate to user
- Architecture docs are incomplete → Flag in handoff, proceed with assumptions documented

### Phase 2: Programmer

**Input:** `agents/planner/handoff.md`, all `docs/*.md` files
**Output:** `agents/programmer/handoff.md`, all source code, manifests, configs

The programmer implements everything specified by the planner:

1. Read planner's task breakdown and acceptance criteria
2. Implement in the order specified by planner
3. For each task:
   - Implement the code/config/manifest
   - Verify it works locally (where possible)
   - Note any deviations from spec with rationale
4. Write `agents/programmer/handoff.md` with:
   - What was built (file-by-file)
   - Decisions made (with rationale)
   - Known issues or limitations
   - Deployment instructions
5. Update `pipeline-state.md`

**Escalation triggers:**
- Spec is ambiguous or contradictory → Escalate to planner
- Technical infeasibility → Escalate to planner with alternative proposal
- Missing dependency → Escalate to planner

### Phase 3: Tester

**Input:** `agents/programmer/handoff.md`, `agents/planner/handoff.md` (for acceptance criteria)
**Output:** `agents/tester/handoff.md`

The tester validates every acceptance criterion:

1. Read planner's acceptance criteria
2. Read programmer's implementation notes
3. For each acceptance criterion:
   - Design a test (unit, integration, or manual verification)
   - Execute the test
   - Record result: PASS, FAIL, or BLOCKED
   - If FAIL: Record expected vs actual, steps to reproduce
4. Run all 6 demo scenarios end-to-end
5. Verify egress policy enforcement
6. Write `agents/tester/handoff.md` with:
   - Test results per acceptance criterion
   - Demo scenario results
   - Bug reports (if any)
   - Coverage assessment
7. Update `pipeline-state.md`

**Escalation triggers:**
- Test failure → Escalate to programmer with bug report
- Environment issue → Escalate to programmer
- Acceptance criterion is untestable → Escalate to planner

### Phase 4: Reviewer

**Input:** All previous `handoff.md` files, deployed system state
**Output:** `agents/reviewer/handoff.md`, `agents/reviewer/scorecard.md`

The reviewer is the final quality gate:

1. Read all previous handoffs
2. Independently verify a sample of test results
3. Score against the rubric (see `agents/reviewer/scorecard.md`)
4. Check for:
   - Security vulnerabilities (leakage paths, bypass vectors)
   - Architecture integrity (trust boundaries respected)
   - Red Hat alignment (upstream OSS, OpenShift-native)
   - Demo readiness (all scenarios work, narrative is clear)
5. Produce a final score and decision:
   - **APPROVED** (score >= 0.80, no dimension below threshold)
   - **CONDITIONAL** (score >= 0.70, specific remediations required)
   - **REJECTED** (score < 0.70 or critical security issue)
6. Write `agents/reviewer/scorecard.md` with evidence
7. Write `agents/reviewer/handoff.md` with decision
8. Update `pipeline-state.md`

**Escalation triggers:**
- Critical security issue → Escalate to programmer with specific fix
- Architecture violation → Escalate to planner for redesign
- Missing demo scenario → Escalate to programmer
- Scoring anomaly → Re-evaluate with additional evidence

## Handoff Format

Every `handoff.md` follows this structure:

```markdown
# [Agent Role] Handoff

## Status: COMPLETE | BLOCKED | ESCALATED

## Summary
One paragraph: what this agent did and the key outcome.

## What Was Done
- Bullet list of completed work items

## Decisions Made
- Decision: [what was decided]
  - Rationale: [why]
  - Alternatives considered: [what else was evaluated]

## Issues Found
- Issue: [description]
  - Severity: CRITICAL | HIGH | MEDIUM | LOW
  - Impact: [what it affects]

## Escalation (if Status is BLOCKED or ESCALATED)
- **Target agent:** [Planner | Programmer | Tester | Reviewer]
- **Issue:** [what needs resolution]
- **Context:** [relevant details]
- **Suggested resolution:** [recommendation]

## Acceptance Criteria Status
| ID | Criterion | Status | Evidence |
|----|-----------|--------|----------|
| AC-1 | [description] | PASS/FAIL/BLOCKED | [link or explanation] |
```

## Pipeline State Tracking

`pipeline-state.md` is the single source of truth for pipeline progress. Each agent updates it when they start and finish. Format:

```markdown
| Phase | Agent | Status | Started | Completed | Notes |
|-------|-------|--------|---------|-----------|-------|
| 1 | Planner | PENDING | — | — | — |
| 2 | Programmer | PENDING | — | — | — |
| 3 | Tester | PENDING | — | — | — |
| 4 | Reviewer | PENDING | — | — | — |
```

## Iteration Protocol

If the reviewer returns CONDITIONAL:
1. Reviewer specifies exact remediations in `handoff.md`
2. Pipeline re-enters at the appropriate phase (usually Programmer)
3. The re-entering agent reads the reviewer's escalation
4. The agent addresses only the specified remediations
5. Pipeline continues forward from that point
6. Maximum 3 iterations before human intervention required

## Human Intervention Points

The pipeline pauses for human input at these points:
- **Before Phase 1:** User provides/confirms requirements (already done via research.md and this prompt)
- **After Phase 4 (REJECTED):** User decides whether to continue or pivot
- **After 3 iterations:** User reviews systemic issues
- **Any CRITICAL escalation:** User is notified immediately
