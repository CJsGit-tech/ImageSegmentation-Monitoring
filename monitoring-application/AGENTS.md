<!-- linearboard:init:begin -->
# LinearBoard project workflow

**Configured project:** `MonitoringDashboard`
**Skill:** [LinearBoard](/Users/chuang/.codex/skills/linearboard/SKILL.md)
**Review method:** [linked-record change review](/Users/chuang/.codex/skills/linearboard/references/change-review.md)

Use the skill as the command/template authority. This block is engineering
guidance, not a copy of the project’s product documents. Preserve existing
repository instructions outside this block and resolve substantive conflicts
before acting.

## Scope and command selection

Resolve the exact project, repository/application, milestone, selected issues
and environment before project work. A directory or open browser does not
select a project. Ask one focused question if the destination is ambiguous.
Keep multiple applications and nested instructions distinct.

Use the skill router to choose the first incomplete applicable stage and reuse
confirmed work. `init` configures this local environment; `help` explains syntax.
Neither runs product workflows. Creation, style selection, complete-set
approval, implementation dispatch, deployment and final acceptance are separate
gates. Do not advance simply because the preceding stage finished.

The command list below maps responsibilities, not independent authorization.
The main agent routes all commands. Specialists work only on delegated scope;
simple planning and questions need no crew.

main
- role: Routes commands, resolves scope, coordinates specialists and owns integration, verification and authorized record operations.
- commands: init, help, planning, create-vision-document, create-technical-baseline, create-milestones, create-PRDs, create-designs, create-visuals, create-issues-FE, create-issues-BE, organize-task-batch, dispatch, debug, sync

requirements_analyst
- role: Reviews requirements, role boundaries and linked-record impacts, then proposes focused clarification or sync questions.
- commands: planning, create-vision-document, create-technical-baseline, create-milestones, create-PRDs, sync

repo_explorer
- role: Traces the selected application’s real code, dependencies, contracts and checks to support delivery planning.
- commands: create-technical-baseline, create-issues-FE, create-issues-BE, organize-task-batch, dispatch, debug

visual_designer
- role: Builds isolated interactive design proposals and review galleries within the currently authorized design stage.
- commands: create-designs, create-visuals, sync

frontend_delivery
- role: Investigates frontend delivery choices and implements only explicitly authorized UI issues or bounded fixes.
- commands: create-issues-FE, dispatch, debug

backend_delivery
- role: Investigates backend contracts and implements authorized service, authorization and data-consistency work.
- commands: create-issues-BE, dispatch, debug

delivery_reviewer
- role: Independently verifies delivery and owns the resulting LinearBoard ticket status.
- commands: dispatch, debug, sync

ui_verifier
- role: Operates scoped UI journeys and compares their behavior and presentation with exact approved references.
- commands: create-designs, create-visuals, dispatch, debug, sync

## Coordination and board ownership

Use at most three concurrent subagents and respect a lower configured limit.
Give every writer non-overlapping files and tell it to preserve others’ edits.
Announce role, task name, scope and ownership before spawning. Use task names
`<role>__<issue_or_scope>__<responsibility>`. Only the main agent delegates.

Production implementers and mockup writers first inspect their sources and
return an acknowledged plan or precise blocker without editing. The main agent
validates the plan and explicitly releases execution. This is an internal
checkpoint, not a repeated user-approval request. Read-only specialists return
findings directly. Worker reports are claims for independent verification.

The main agent writes ordinary workflow records. The assigned delivery
reviewer owns verification evidence and status for the tickets in its review
scope. The main agent covers that role when no reviewer is assigned. Debug
implementers write only their own BUG when authorized.

LinearBoard remains the sole product-work store: visions, milestones, stories,
PRDs, decisions, approvals, relations, Views and delivery evidence stay there.
Do not create local PM mirrors, sync ledgers or report archives. Keep source,
tests, configuration and allowed isolated visual-review bundles locally.
Preserve M-number tags, stable story anchors, native relationships, Category
and applicable Coder/Batch labels. Keep tracking JSON out of document bodies.

## Linked-document checks and impact review

Before drafting/updating, inspect the owning current template and canonical
sources. After a permitted save, read back content, native associations,
labels/relations and rendered presentation as required by that command.
Use the skill’s existing traceability audit for relevant story/PRD/milestone
checks; audit does not authorize its repair mode.

Review impact when PRDs are created/revised, a clarification changes a rule,
the user requests scope/behavior changes, or design/delivery exposes a conflict.
Read the before/after behavior. Follow relevant native links, story anchors and
document references in both directions, then search the scoped project for
the old rule or capability to catch missing links. Review only affected records:
vision or milestone commitments, owning PRDs, shared baseline, design specs,
delivery issues and their checks. Do not scan unrelated projects.

For each material difference report the triggering decision/source, exact
record/section, current versus proposed behavior, and whether it is confirmed,
proposed, conflicting or unverified. Explain affected acceptance criteria and
dependencies. Keep product choices separate from engineering choices.

After `create-PRDs`, begin its built-in consequential clarification loop; reuse
answered questions and ask one at a time. Apply and verify an authorized answer
in its owning PRD body and comment. Never invent a separate clarification
command or delay an already authorized owning-record update unnecessarily.

For changes outside the active command’s authority, recommend the existing
`sync <Project> <Milestone> [scope or new request]` workflow and show concrete
record-by-record from/to proposals. Wait for approval of the presented edits.
An earlier product decision is not approval of every linked rewrite. Reuse
approval already covering the exact edits. Pause dependent work for material
unresolved conflicts; continue independent authorized work. Do not rewrite a
requirement to match a worker’s implementation.

These are workflow checks and review instructions. Initialization does not
install automatic hooks, a background document watcher or a new validator.
Do not claim enforcement or a passing audit that was not actually run.

## Design, verification and handoff

Follow the current skill’s design method and exact selected direction.
Keep mockups isolated and deliver an interactive HTML gallery with reachable
screens/states. Style selection is not complete-set approval. Only after the
applicable approval upload confirmed JPGs to their owning Design issues and
verify the supported approval workflow. Production captures are evidence,
not newly approved designs.

For implementation, operate the actual scoped target and compare applicable
routes/roles/states/sizes with approved references. A responsive browser is not
a separate native mobile implementation. Distinguish fixture/adaptor checks,
real hosted data and provider-managed screens. Attribute limitations with
evidence; never turn an untested criterion into Pass.

The delivery reviewer verifies the actual target and records the result on
the owning ticket. Follow the shared skill’s review policy. Move verified
tickets to Done using the native UI and move tickets with a required check
blocked by test restrictions to Todo. Route failed checks for remediation.
Read the resulting status back. Deployment and consequential live-data
changes require authorization covering that target and action. Protect
secrets in all tools, source, logs and screenshots.

End each LinearBoard command response with one standalone **Recommended next step:**
line using the skill router. Preserve the verified batch or explicitly selected
issue scope. Use an exact supported invocation for a new command. If the
current command is waiting for a reply, name the reply that resumes it.
A recommendation grants no authority to execute.
<!-- linearboard:init:end -->
