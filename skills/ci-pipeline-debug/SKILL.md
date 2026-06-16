---
name: ci-pipeline-debug
description: Debugs failing GitHub Actions pipelines on the self-hosted Yocto runners
             (<ci-runner-ip>, ci user, <runner-agent> / ><runner-agent-2>). Covers fetching
             failed job logs, identifying error patterns, fixing stale runner workspaces,
             submodule mismatches, sstate version conflicts, and triggering reruns.
             Use when a pipeline fails or behaves unexpectedly on the Roomboard CI.
---

# Skill: CI Pipeline Debug

## Environment

| Item | Value |
|------|-------|
| Runner host | `<ci-runner-ip>` (SSH as `ci` user) |
| HW1 runner | `~/<runner-agent>/` — label `hw1-yocto` |
| HW2 runner | `~/><runner-agent-2>/` — label `hw2-yocto` |
| Build dirs | `~/<runner-agent>/_work/roomboard-linux/builds/<build-dir-hw{1,2}>/` |
| Repo | `custom-repo/roomboard-linux` on `github.com` |
| GH CLI | Always pass `GH_HOST=github.com` |

---

## Step 1 — Get the failed run and logs

```bash
# List recent runs
GH_HOST=github.com gh run list --repo custom-repo/roomboard-linux --limit 10

# View a specific run summary
GH_HOST=github.com gh run view <RUN_ID> --repo custom-repo/roomboard-linux

# Get failed job logs only (most useful first step)
GH_HOST=github.com gh run view <RUN_ID> --repo custom-repo/roomboard-linux --log-failed | cat

# Filter for actionable lines
GH_HOST=github.com gh run view <RUN_ID> --repo custom-repo/roomboard-linux --log-failed \
  | grep -E 'ERROR|error:|bad sub|exit code|FAILED|fatal' | head -30
```

---

## Step 2 — Identify the error pattern

### Pattern: `_run_docker:68: bad substitution`
**Cause:** `docker_utils.sh` uses `${!_var}` (bash indirect expansion) but is sourced
inside `zsh`. Fixed in `docker-yocto-env` at commit `f1dacb5`.

**Fix:** Update the submodule in ALL build dirs on the runners AND in the branch's
committed submodule pointer.

```bash
# Update on runner build dirs
ssh ci@<ci-runner-ip> "
for dir in \
  ~/<runner-agent>/_work/roomboard-linux/builds/<build-dir-hw1> \
  ~/<runner-agent>/_work/roomboard-linux/builds/<build-dir-hw2> \
  ~/><runner-agent-2>/_work/roomboard-linux/builds/<build-dir-hw2>; do
  echo \"--- \$dir ---\"
  cd \"\$dir\" && git submodule update --remote docker-yocto-env && echo ok
done"

# Also bump the submodule pointer in the git branch
cd ~/ws/platform/<repo>
git checkout <branch>
git add docker-yocto-env
git commit -m "chore: bump docker-yocto-env to fix zsh bad substitution"
git push origin <branch>
```

> **Gotcha — `--rerun --failed` reuses stale workspace.**
> GitHub Actions reruns do NOT re-checkout. If the build dir has stale files, you must
> manually update them on the runner AND push a new commit to trigger a fresh run.

---

### Pattern: `Unable to find file file:///workspace/<file>.tgz`
**Cause:** A required local file (e.g. `cst-3.1.0.tgz`) is missing from the build dir.
These are LFS-tracked assets that must exist at the workspace root on the runner.

**Fix:**
```bash
# Check where the file exists
ssh ci@<ci-runner-ip> "find ~ -maxdepth 5 -name 'cst-3.1.0.tgz' 2>/dev/null"

# Copy from another build dir if available
ssh ci@<ci-runner-ip> "cp ~/<runner-agent>/_work/roomboard-linux/builds/<build-dir-hw1>/cst-3.1.0.tgz \
  ~/><runner-agent-2>/_work/roomboard-linux/builds/<build-dir-hw2>/"

# If missing entirely — restore from git LFS history
cd ~/ws/platform/<repo>
git checkout <last-good-commit> -- cst-3.1.0.tgz
git add cst-3.1.0.tgz
git commit -m "revert: restore <file> accidentally removed"
git push origin <branch>   # may need branch protection temporarily disabled
```

---

### Pattern: `version-going-backwards` (QA Issue)
**Cause:** The sstate cache has packagedata at revision `rN.1` (from a previous
PRSERV-bumped build), but the current build produces `rN.0`. BitBake treats this as a
fatal QA error.

**Fix:** Clear the affected recipe's sstate on the runner:
```bash
ssh ci@<ci-runner-ip> "
BUILD_DIR=~/><runner-agent-2>/_work/roomboard-linux/builds/<build-dir-hw2>
cd \"\$BUILD_DIR\"
zsh -c '. ./env > /dev/null && poky run <build-target> \
  bitbake linux-variscite -c cleansstate'
"
```
Then rerun the pipeline.

---

### Pattern: `error: corrupt patch at line N`
**Cause:** The unified diff hunk header `@@ -old,COUNT +new,COUNT @@` does not match the
actual number of lines in the hunk body. `git apply` / kern-tools stops reading early and
reports the first unexpected line as "corrupt".

**Diagnosis:** Count every ` ` (context), `-` (removed), and `+` (added) line in the hunk.
Old-side count = context + removed lines. New-side count = context + added lines. Compare to
the header. If they differ, the header is wrong.

**Fix:** Update the `@@` header to the correct counts:
```bash
# In the patch file, change:
# @@ -700,23 +702,43 @@
# to the correct values, e.g.:
# @@ -700,27 +702,46 @@
```

After fixing, commit and push to **both** remotes (`origin` Azure DevOps AND `custom-repo` GHE),
then update the runner build dirs and bump the submodule pointer in the branch.

---

### Pattern: `do_kernel_configme: No BSP entry point found`
**Cause:** This latent bug in `linux-variscite` is always masked by sstate. It only surfaces
after a `cleansstate` or completely clean build. Root cause: `unset KBUILD_DEFCONFIG` in the
`.bbappend` is shell syntax — it does nothing in BitBake. `do_kernel_configme` finds no BSP
entry point and no config fragments → fails.

**Fix:** Override the task to a no-op in `linux-variscite_%.bbappend`:
```bitbake
do_kernel_configme() {
    :
}
```
`do_configure:prepend` already handles defconfig selection correctly.

---

### Pattern: `cleansstate` cascades into full kernel rebuild and exposes hidden bugs
**Cause:** `bitbake linux-variscite -c cleansstate` also runs `-c clean`, which deletes the
work dir AND deploy artifacts (`Image.gz`, DTBs). This triggers the deploy-health recovery in
`sign_bootloader.sh` which runs `bitbake virtual/kernel -c deploy -f`. The forced rebuild
exposes bugs previously masked by sstate (e.g. `do_kernel_configme`, `do_patch`).

**Before clearing kernel sstate, check for running builds:**
```bash
ssh ci@<ci-runner-ip> "docker ps --format '{{.Names}} {{.Status}}'"
```
Wait for any active container to finish. Then clear sstate only if necessary.

---
**Cause:** The `Detect Yocto file changes` job uses `git diff --name-only` which outputs
bare submodule names (e.g. `meta-custom-repo`) without trailing slash. The regex patterns
use `^meta-custom-repo/` (with slash) and never match.

**Fix:** Add exact-match patterns for submodule names in `.github/workflows/build-yocto.yml`:
```yaml
|| echo "$changed" | grep -qE \
  '^(meta-custom-repo|meta-custom-repo-arm|docker-yocto-env)$' \
```

---

### Pattern: `do_image_mender: Rootfs image ...ext4 not found`
**Cause:** `IMAGE_CLASSES:append:<machine> = " mender-custom-repo-image"` in
`local.conf` is applied globally to ALL images including `initramfs-image-custom-repo`,
which has no `ext4` in `IMAGE_FSTYPES`.

**Fix options:**
1. Move `IMAGE_CLASSES` append into `core-image-custom-repo.bb` directly (recommended)
2. Add a guard in `mender-custom-repo-image.bbclass` to skip when no `ext4` fstype

---

## Step 3 — Rerun strategies

```bash
# Rerun only failed jobs (uses existing workspace — only works if fix is already on runner)
GH_HOST=github.com gh run rerun <RUN_ID> --repo custom-repo/roomboard-linux --failed

# Trigger a completely fresh run (new checkout, clean workspace)
# Push a new commit to the branch — even an empty one:
git commit --allow-empty -m "ci: trigger clean pipeline run"
git push origin <branch>
```

> **When to use which:**
> - `--rerun --failed`: only when the fix is already applied to the runner's build dirs
>   (e.g. you manually updated files on the runner)
> - Empty commit push: when the fix is in git and you need a fresh checkout

---

## Step 4 — Inspect runner workspace directly

```bash
# Find all docker-yocto-env copies and check their commit
ssh ci@<ci-runner-ip> "find ~/<runner-agent> ~/><runner-agent-2> -name 'docker-yocto-env' -type d 2>/dev/null"

# Check submodule commit in a build dir
ssh ci@<ci-runner-ip> "git -C ~/<runner-agent>/_work/roomboard-linux/builds/<build-dir-hw1> submodule status docker-yocto-env"

# List build dir contents
ssh ci@<ci-runner-ip> "ls ~/<runner-agent>/_work/roomboard-linux/builds/<build-dir-hw1>/"

# Check sstate dir location
ssh ci@<ci-runner-ip> "grep SSTATE_DIR ~/><runner-agent-2>/_work/roomboard-linux/builds/<build-dir-hw2>/<build-target>/conf/local.conf"
```

---

## `meta-custom-repo-arm` remote setup

Single remote `origin` → `https://github.com/custom-repo/meta-custom-repo-arm.git` (GitHub Enterprise).

Update runner build dirs by explicit SHA (not `--remote`, which may resolve the wrong branch if tracking is misconfigured):
```bash
ssh ci@<ci-runner-ip> "
for dir in \
  ~/<runner-agent>/_work/roomboard-linux/builds/<build-dir-hw2>/meta-custom-repo-arm \
  ~/><runner-agent-2>/_work/roomboard-linux/builds/<build-dir-hw2>/meta-custom-repo-arm; do
  git -C \"\$dir\" fetch origin
  git -C \"\$dir\" checkout <SHA>
  git -C \"\$dir\" log --oneline -1
done"
```

---

## Step 5 — CI fixes

**Never push directly to `kirkstone-dev` or any protected branch.** Always use a feature branch + PR:

```bash
git checkout -b fix/ci-<short-description>
git commit -m "ci: <fix description>"
git push -u origin fix/ci-<short-description>
gh pr create --base kirkstone-dev --title "ci: <fix description>" --body "<what and why>"
```

---

## Quick reference — common `gh` commands

```bash
# List runs for a branch
GH_HOST=github.com gh run list --repo custom-repo/roomboard-linux --branch <branch> --limit 5

# View job logs
GH_HOST=github.com gh run view --job=<JOB_ID> --repo custom-repo/roomboard-linux --log

# Get PR info (base/head SHAs, branch names)
GH_HOST=github.com gh pr view <PR_NUM> --repo custom-repo/roomboard-linux \
  --json headRefName,baseRefName,headRefOid,baseRefOid

# Post a comment on a PR
GH_HOST=github.com gh pr comment <PR_NUM> --repo custom-repo/roomboard-linux --body "..."

# Check what files changed between PR base and head
cd ~/ws/platform/<repo>
git fetch origin
git diff <baseRefOid>..<headRefOid> --name-only
```
