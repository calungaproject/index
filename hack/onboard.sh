#!/bin/bash
set -euo pipefail

usage() {
    cat >&2 <<'USAGE'
Usage:
  hack/onboard.sh <package> [version]            Full lifecycle (create + wait + merge)
  hack/onboard.sh create <package> [version]     Create branch, commit, push, open PR
  hack/onboard.sh wait <pr-url>                  Wait for CI checks to pass
  hack/onboard.sh merge <pr-url>                 Merge PR and clean up branch

If [version] is omitted, the latest non-yanked version from PyPI is used.
USAGE
    exit 1
}

log() { echo "==> $*" >&2; }

# ---------------------------------------------------------------------------
# create: prerequisites + steps 1-4, returns to main, prints PR URL to stdout
# ---------------------------------------------------------------------------
cmd_create() {
    local PACKAGE="${1:?create requires a package name}"
    local REQUESTED_VERSION="${2:-}"
    local PACKAGES_DIR="onboarded_packages"
    local PKG_FILE="$PACKAGES_DIR/$PACKAGE.json"
    local BRANCH="onboard/$PACKAGE"

    log "Checking gh authentication..."
    gh auth status >/dev/null 2>&1 || { echo "ERROR: gh is not authenticated. Run 'gh auth login' first." >&2; exit 1; }

    log "Switching to main and pulling latest..."
    git checkout main >&2
    git pull >&2

    log "Checking for uncommitted changes to tracked files..."
    if ! git diff --quiet || ! git diff --cached --quiet; then
        echo "ERROR: There are uncommitted changes to tracked files. Commit or stash them first." >&2
        exit 1
    fi

    if [[ -f "$PKG_FILE" ]]; then
        echo "ERROR: $PKG_FILE already exists. Package is already onboarded." >&2
        exit 1
    fi

    log "Running onboarding script for $PACKAGE..."
    local onboard_args=("$PACKAGE")
    if [[ -n "$REQUESTED_VERSION" ]]; then
        onboard_args+=("$REQUESTED_VERSION")
    fi
    python hack/onboard_package.py "${onboard_args[@]}" >&2

    local VERSION
    VERSION="$(jq -r '.version' "$PKG_FILE")"
    if [[ -z "$VERSION" || "$VERSION" == "null" ]]; then
        echo "ERROR: Failed to extract version from $PKG_FILE" >&2
        exit 1
    fi
    log "Onboarded version: $VERSION"

    log "Creating branch $BRANCH..."
    git checkout -b "$BRANCH" >&2

    log "Committing $PKG_FILE..."
    git add "$PKG_FILE"
    git commit -m "$(cat <<EOF
Onboard package $PACKAGE

Generated-by: Claude Code
EOF
)" >&2

    log "Pushing branch and creating PR..."
    git push -u origin "$BRANCH" >&2

    # pr creation can fail due to API slowness
    sleep 3

    local PR_URL
    PR_URL="$(gh pr create \
        --title "Onboard $PACKAGE" \
        --label "pkg onboarding" \
        --body "$(cat <<EOF
## Summary
- Onboard $PACKAGE at version $VERSION
EOF
)")"

    log "PR created: $PR_URL"

    log "Returning to main..."
    git checkout main >&2

    echo "$PR_URL"
}

# ---------------------------------------------------------------------------
# wait: step 5 — block until CI checks complete
# ---------------------------------------------------------------------------
cmd_wait() {
    local PR_URL="${1:?wait requires a PR URL}"

    log "Waiting for CI checks on $PR_URL..."

    # Wait for checks to appear (Konflux takes time to register)
    local attempts=0
    while [ $attempts -lt 30 ]; do
        local check_output
        check_output="$(gh pr checks "$PR_URL" 2>&1)" || true
        if echo "$check_output" | grep -q "wheel-check"; then
            break
        fi
        log "Checks not yet reported, waiting... (attempt $((attempts+1))/30)"
        sleep 10
        attempts=$((attempts + 1))
    done

    if [ $attempts -ge 30 ]; then
        echo "ERROR: Checks never appeared for $PR_URL" >&2
        exit 1
    fi

    # Poll for completion, ignoring sourcery and enterprise-contract checks
    while true; do
        local all_done=1
        while IFS= read -r line; do
            if echo "$line" | grep -q "enterprise-contract"; then continue; fi
            if echo "$line" | grep -q "sourcery"; then continue; fi
            if echo "$line" | grep -q "pending\|running"; then
                all_done=0
                break
            fi
        done <<< "$(gh pr checks "$PR_URL" 2>&1)"
        if [ $all_done -eq 1 ]; then break; fi
        sleep 15
    done

    # Verify all wheel checks passed
    local final_checks
    final_checks="$(gh pr checks "$PR_URL" 2>&1)"
    local failed=0

    while IFS= read -r line; do
        # Skip enterprise contract (expected to skip on PRs)
        if echo "$line" | grep -q "enterprise-contract"; then
            continue
        fi
        # Skip sourcery.ai check
        if echo "$line" | grep -q "sourcery"; then
            continue
        fi
        # Check wheel-check lines for failure
        if echo "$line" | grep -q "wheel-check" && echo "$line" | grep -qv "pass"; then
            echo "FAILED CHECK: $line" >&2
            failed=1
        fi
        # Check pipeline run
        if echo "$line" | grep -q "on-pull-request" && echo "$line" | grep -q "fail"; then
            echo "FAILED CHECK: $line" >&2
            failed=1
        fi
    done <<< "$final_checks"

    if [ $failed -eq 1 ]; then
        echo "ERROR: Some checks failed. Review at: $PR_URL" >&2
        gh pr checks "$PR_URL" >&2
        exit 1
    fi

    log "All checks passed for $PR_URL"
}

# ---------------------------------------------------------------------------
# merge: steps 6-7 — merge PR and clean up local branch
# ---------------------------------------------------------------------------
cmd_merge() {
    local PR_URL="${1:?merge requires a PR URL}"

    local BRANCH
    BRANCH="$(gh pr view "$PR_URL" --json headRefName -q .headRefName)"

    log "Merging PR (rebase): $PR_URL..."
    gh pr merge "$PR_URL" --rebase

    log "Cleaning up..."
    git checkout main >&2
    git pull >&2
    git branch -d "$BRANCH" 2>/dev/null || true

    log "Done. Merged $PR_URL"
}

# ---------------------------------------------------------------------------
# main: dispatch subcommand or run full lifecycle
# ---------------------------------------------------------------------------
case "${1:-}" in
    create)
        shift
        cmd_create "$@"
        ;;
    wait)
        shift
        cmd_wait "$@"
        ;;
    merge)
        shift
        cmd_merge "$@"
        ;;
    -h|--help|"")
        usage
        ;;
    *)
        PACKAGE="$1"
        VERSION="${2:-}"
        PR_URL="$(cmd_create "$PACKAGE" "$VERSION")"
        cmd_wait "$PR_URL"
        cmd_merge "$PR_URL"
        echo "==> Successfully onboarded $PACKAGE" >&2
        ;;
esac
