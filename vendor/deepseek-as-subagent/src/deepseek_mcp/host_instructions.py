"""Stable MCP host instructions, separated to keep the server entrypoint small."""

HOST_INSTRUCTIONS = """
Recovery records cover Write/Edit/NotebookEdit commits; trusted-host Bash changes
are not transaction-journaled. After any result with reported mutations—or
cancellation, disconnection, or restart—call `get_deepseek_recovery`, verify the
reported files, then call `acknowledge_deepseek_mutations(transaction_ids)` with
the exact reviewed IDs. If coding Bash may have run before an interruption,
inspect the workspace independently before continuing.
After every delegation, verify delegated output against the requested acceptance
criteria before relying on it.
CodexRelay adds optional task_root, write_paths and token_budget to delegation
tools. Use a specific task directory and explicit allowed output files for batch
formatting/refactoring; a directory-wide formatter can also process Markdown.
Use structured Bash executable/args/cwd for normal programs instead of probing
Windows shell quotes. Two equivalent failures without changed inputs stop a run.
On no_progress/task_scope_violation, inspect existing changes and recovery, then
finish the remaining focused work as the parent instead of replaying the full job.
Read telemetry even after failure/cancellation; reported provider usage and local
estimated budget are separate. workspace_changes is an observed before/after diff,
including command changes, not a transaction journal or a filesystem sandbox.
Completed means execution ended. CodexRelay's record_deepseek_verification records
the parent's independently checked verdict and evidence; never mark model claims
verified without inspecting actual files/tests. A queued steering message is not
yet applied; telemetry reports the count of applied steering messages.

API reference:
- `ping()`: check that the MCP server is available.
- `delegate_to_deepseek(task, context="", model="flash")`: wait for full coding to
  finish; it cannot be steered, queried, or cancelled while running.
- `delegate_to_deepseek_readonly(task, context="", model="flash")`: wait for
  file-only analysis with Read/Glob/Grep; it has no Bash or mutation tools and
  cannot be controlled while running.
- `start_deepseek(task, context="", model="flash")` /
  `start_deepseek_readonly(task, context="", model="flash")`: start a coding or
  read-only background job and return `job_id`.
- `get_deepseek_status(job_id)`: read a background job's state.
- `send_deepseek_message(job_id, message)`: add or correct its task instruction.
- `cancel_deepseek(job_id)`: cancel a background job.
- `get_deepseek_result(job_id)`: return its final result, or not-ready state.
- `get_deepseek_recovery()`: list unacknowledged mutations from coding work.
- `acknowledge_deepseek_mutations(transaction_ids)`: acknowledge exact reviewed IDs.
`task` states the goal and acceptance criteria; optional `context` supplies paths,
constraints, and project conventions. `model` is exactly `flash` or `pro`: omit it
for Flash. A background job keeps the model chosen at start; steering cannot change
it. `job_id` comes from `start_*`.

Model routing guidance:
- `flash`: default general-purpose subagent, roughly Sonnet/Terra-tier. Use it for
  normal coding, review, investigation, refactoring, and routine multi-file work.
- `pro`: stronger difficult-task subagent, roughly Opus/Sol-tier. Use it for complex
  debugging, architecture, difficult multi-file reasoning, or when Flash was
  insufficient.
Do not select Pro merely because it is available; prefer Flash unless the task
clearly benefits from the stronger tier.

Selection: use `delegate_*` when the host can wait for completion. Use `start_*`
when it needs steering, status, or cancellation. Use readonly only for pure
reading/search/review of existing files or text when no task step needs a command;
use coding for everything else or uncertainty. The API freezes the capability for
the job. Steering cannot enable Bash or mutation tools: if a readonly job later
needs either, cancel or finish it and create a new coding job.

DeepSeek cannot see host chat or project instructions; pass needed context
explicitly. One OS lease permits one DeepSeek execution per canonical workspace,
but it cannot prevent the host, IDE, or other processes from editing that workspace.
While a coding background job is running, the host should steer, query, or cancel
that job instead of independently mutating the same workspace; resume host-side
edits after the job reaches a terminal state. Background jobs/results are
process-local. New delegation fails closed while recovery records remain.
""".strip()
