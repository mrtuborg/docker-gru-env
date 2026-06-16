# Copilot Instructions

<!-- This is the built-in fallback. Override by placing .github/copilot-instructions.md
     in your workspace repo. The container will prefer your file over this one. -->

## Operating mode

You are running inside an automated, non-interactive container session.
You cannot ask the user questions. When something is ambiguous:
- State your assumption as a comment on the issue and proceed.
- If you hit a hard blocker that requires human input, add the `needs-human` label,
  post a comment explaining what is needed, and stop.

## General rules

- Work **only** on the issue you were given. Never read, comment on, or modify other issues.
- Never move an issue to `Todo`, `On Hold`, `Integration`, or `Done` — those are human-only.
- Keep commits small and focused. Write clear commit messages.
- Prefer making changes in tests first (TDD) when practical.
- Do not introduce new dependencies without a clear reason noted in the issue comment.
- When in doubt, do less and leave a detailed comment rather than guessing.

## Git — branching

- **Always create a new branch** for your work. Never commit directly to `main`, `master`,
  or any existing branch that was checked out when the session started.
- Branch naming convention — pick the prefix that matches the issue type:
  - `feat/<short-description>` — new feature or capability
  - `fix/<short-description>` — bug fix
  - `docs/<short-description>` — documentation only
- Use the issue number in the name when one is available:
  `feat/42-add-retry-logic`, `fix/17-null-pointer-on-startup`
- Push the branch and open a draft PR against `main` (or the repo's default branch)
  before starting substantive work, so progress is visible.
- Commit and push at logical checkpoints — do not leave everything until the end.
- Always run existing tests before and after your changes.

## Communication

- Post a comment on the issue at the start of each major step so progress is visible.
- Post a final summary comment in this format before closing the session:
  ```
  ## What was done
  ## Decisions taken
  ## How to verify
  ## Known limitations
  ```

## Session end

Run the `session-handoff` skill when your work is complete.
