# Rebase and merge conflicting version-update PRs

Run `hack/rebase-version-prs` to rebase and merge conflicting automated-build PRs.

## Arguments

$ARGUMENTS

Translate arguments into the appropriate CLI flags:

- PR numbers (e.g. `4847 4867 4565`) → `hack/rebase-version-prs 4847 4867 4565`
- A package name (e.g. `praisonaiagents`) → `hack/rebase-version-prs --package praisonaiagents`
- `all` or no arguments → `hack/rebase-version-prs --all`

Run the command and report the output to the user. If any PRs fail or are skipped, include the details from the summary.
