# Rebase and merge conflicting version-update PRs

You are resolving merge conflicts on automated build PRs that update package versions in `onboarded_packages/*.json` files. These PRs conflict because they each change the `version` field in the same JSON file, and only one can merge at a time.

## Arguments

$ARGUMENTS

If arguments are provided, interpret them as:
- PR numbers (e.g. `4847 4867 4565`) — process only those PRs
- A package name (e.g. `praisonaiagents`) — process all open conflicting PRs for that package
- `all` or no arguments — find all open PRs labeled "automated build" with merge conflicts and process them

## Key context

- The `version` field in each JSON file only controls which version gets built next. It is NOT a record of the "current" version. Downgrades are acceptable and expected during this process.
- Each PR changes only the `version` line. The conflict is always between the version on `main` and the version the PR wants to set.
- When resolving conflicts, always accept the PR's target version (the incoming/theirs side).
- After each PR merges to main, the next PR for the same package will conflict again — this is expected and is why we process sequentially.
- The order in which PRs are processed within a package does not matter for correctness — all versions will be built regardless. Sorting by version ascending is a reasonable default but not a requirement.

## Merge-bot check logic

Use the same CI verification as `hack/merge-bot`:

- **Konflux app slug**: `red-hat-konflux-kflux-prd-rh03`
- **Minimum checks**: 7
- Query: `gh api "repos/{REPO}/commits/{SHA}/check-runs" --jq '[.check_runs[] | select(.app.slug == "red-hat-konflux-kflux-prd-rh03")]'`
- **Guard 1**: At least 7 check-runs reported
- **Guard 2**: All checks completed (`status == "completed"`)
- **Guard 3**: All conclusions are `success` or `neutral`
- **Merge command**: `gh pr merge {PR} --rebase --delete-branch`

## Procedure

### Step 1: Discover PRs

```bash
gh pr list --state open --label "automated build" --json number,title,headRefName,headRefOid,mergeable \
  --jq '.[] | "\(.number)\t\(.title)\t\(.mergeable)"'
```

Filter to PRs where `mergeable` is not `MERGEABLE` (i.e. they have conflicts). Group by package name extracted from the branch name pattern `update-{package}=={version}`. Skip any PRs whose branch names don't match this pattern — they are not automated version-update PRs and should not be processed by this skill. Sort each group by version ascending as a reasonable default ordering.

### Step 2: Process packages

Process one PR per package at a time. When there are multiple packages, you can process one PR from each package in parallel (they touch different JSON files so rebases won't conflict with each other).

For each batch of PRs (one per package):

#### 2a. Rebase
```bash
git fetch origin main
git fetch origin "{branch_name}"
git checkout "{branch_name}" || git checkout -b "{branch_name}" "origin/{branch_name}"
git rebase origin/main
```

#### 2b. Resolve conflict
The conflict is expected to be in `onboarded_packages/{package}.json` on the `version` line only. If the rebase produces conflicts in other files or on lines other than the `version` field, abort the rebase (`git rebase --abort`), skip this PR, and report it for manual resolution — something unexpected changed.

Resolve the version-line conflict by accepting the PR's target version. The conflict markers look like:
```
<<<<<<< HEAD
  "version": "{version_on_main}",
=======
  "version": "{pr_target_version}",
>>>>>>> {commit} (Automatic build {package}=={pr_target_version})
```

Replace the entire conflict block with:
```
  "version": "{pr_target_version}",
```

Then:
```bash
git add onboarded_packages/{package}.json
GIT_EDITOR=true git rebase --continue
```

#### 2c. Force-push
```bash
git push --force-with-lease origin "{branch_name}"
```

#### 2d. Poll CI (every 2 minutes)
Set up a CronCreate job polling every 2 minutes. On each poll:

Determine the repo with `REPO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner')`, then:

```bash
HEAD_SHA=$(gh pr view {PR} --json headRefOid --jq '.headRefOid')
TOTAL=$(gh api "repos/${REPO}/commits/${HEAD_SHA}/check-runs" \
  --jq '[.check_runs[] | select(.app.slug == "red-hat-konflux-kflux-prd-rh03")] | length')
IN_PROGRESS=$(gh api "repos/${REPO}/commits/${HEAD_SHA}/check-runs" \
  --jq '[.check_runs[] | select(.app.slug == "red-hat-konflux-kflux-prd-rh03") | select(.status != "completed")] | length')
FAILING=$(gh api "repos/${REPO}/commits/${HEAD_SHA}/check-runs" \
  --jq '[.check_runs[] | select(.app.slug == "red-hat-konflux-kflux-prd-rh03") | select(.status == "completed") | select(.conclusion != "success" and .conclusion != "neutral")] | length')
```

- If `TOTAL >= 7 AND IN_PROGRESS == 0 AND FAILING == 0`: merge and continue to next PR
- If `FAILING > 0 AND IN_PROGRESS == 0`: **do NOT merge**. Report the failure, delete the cron job, and skip this PR. Never merge with failed wheel checks.
- If still in progress: wait for next poll

#### 2e. Merge
```bash
gh pr merge {PR} --rebase --delete-branch
```

#### 2f. Next PR
After merging, delete the current cron job, fetch main, and rebase the next PR for that package. Repeat from step 2a.

### Step 3: Report

After all PRs are processed (or failed), provide a summary:
- Total PRs processed
- Successfully merged (with PR numbers and versions)
- Failed (with PR numbers, versions, and which checks failed)
- Skipped (if any were filtered out)

## Important rules

- **Never merge a PR with failing checks.** Leave it open and report the failure.
- **One PR per package at a time** — each merge changes main, causing the next PR to conflict.
- **Multiple packages can be processed in parallel** — they touch different JSON files.
- Always use `--force-with-lease` (not `--force`) when pushing.
- Always use `GIT_EDITOR=true git rebase --continue` (not `--no-edit`, which is invalid for rebase).
- The repository has a ruleset forbidding direct pushes to main — all changes must go through PRs.
