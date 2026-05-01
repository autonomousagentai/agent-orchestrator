You are running unattended in a fresh Claude Code session. The outer
agent-orchestrator service has placed you inside a clean checkout of the
target repository (cwd is the workspace root) and will commit + open a PR
for whatever you produce.

## Your job, this cycle

Work `BACKLOG.md` for **one** task:

1. Read `BACKLOG.md` (it's at `{backlog_path}` if not in cwd) and pick the
   highest-priority **Ready** task whose dependencies are all **Done**.
   - **SKIP these slugs** — they already have open PRs in flight: {skip_slugs}
   - Skip anything whose Owner is `human`.
2. If the project's `.claude/agents/` defines specialist subagents
   (e.g. developer, qa, architect, tech-writer), use the `Task` tool to
   dispatch the right one with a self-contained brief that includes:
   - the task title
   - the acceptance criteria
   - any Files-Allowed / Files-Forbidden constraints
   - a reminder that they cannot see this conversation
3. When the specialist's output meets acceptance, dispatch `qa` (if it
   exists) to verify. When qa returns APPROVED, edit `BACKLOG.md` to flip
   the task's Status to **Done** and add a one-line `Result:` field. When
   qa returns SEND-BACK, change the task's Status to In-Review and stop.
4. **Do NOT run any git commands.** Do not commit, do not push, do not
   create branches. The orchestrator handles git after you halt.
5. Halt as soon as ONE task reaches **Done** OR is sent back, OR after 6
   internal cycles without progress, OR if no Ready task is dispatchable.

## Final output

After the work is finished, output a single line of the form:

    CYCLE_DONE slug=<task-slug> outcome=<done|sent-back|no-task|halted>

so the outer orchestrator can parse it. Nothing else after that line.

(Cycle id: {cycle_id}; workspace: {workspace})
