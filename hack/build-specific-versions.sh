#!/bin/bash
set -euo pipefail

usage() {
    cat >&2 <<'USAGE'
Usage:
  hack/build-specific-versions.sh [--only-create-pr] <file>
  hack/build-specific-versions.sh [--only-create-pr] <package==version> [...]

Read package==version entries from a file (one per line) or directly from
command-line arguments. For each entry:
  1. Update onboarded_packages/<package>.json with the new version
  2. Remove the version from ignored_versions if present
  3. Push a branch and open a PR against main
  4. Wait for CI checks to pass and merge the PR (unless --only-create-pr)

Options:
  --only-create-pr   Only create PRs, skip waiting for checks and merging
USAGE
    exit 1
}

log() { echo "==> $*" >&2; }
warn() { echo "WARNING: $*" >&2; }

ONLY_CREATE_PR=false
if [[ "${1:-}" == "--only-create-pr" ]]; then
    ONLY_CREATE_PR=true
    shift
fi

[[ $# -eq 0 ]] && usage

gh auth status >/dev/null 2>&1 || { echo "ERROR: gh is not authenticated. Run 'gh auth login' first." >&2; exit 1; }

INPUT_LINES=()
if [[ $# -eq 1 && -f "$1" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        INPUT_LINES+=("$line")
    done < "$1"
else
    for arg in "$@"; do
        if [[ "$arg" != *==* ]]; then
            echo "ERROR: Invalid argument '$arg'. Expected format: package==version" >&2
            exit 1
        fi
        INPUT_LINES+=("$arg")
    done
fi

PACKAGES_DIR="onboarded_packages"

wait_for_checks() {
    local PR_URL="$1"

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
        warn "Checks never appeared for $PR_URL"
        return 1
    fi

    while true; do
        local all_done=1
        while IFS= read -r line; do
            if echo "$line" | grep -q "enterprise-contract"; then continue; fi
            if echo "$line" | grep -q "sourcery"; then continue; fi
            if echo "$line" | grep -qE "pending|running"; then
                all_done=0
                break
            fi
        done <<< "$(gh pr checks "$PR_URL" 2>&1)"
        if [ $all_done -eq 1 ]; then break; fi
        sleep 15
    done

    local final_checks
    final_checks="$(gh pr checks "$PR_URL" 2>&1)"
    local failed=0

    while IFS= read -r line; do
        if echo "$line" | grep -q "enterprise-contract"; then continue; fi
        if echo "$line" | grep -q "sourcery"; then continue; fi
        if echo "$line" | grep -q "wheel-check" && echo "$line" | grep -qv "pass"; then
            warn "FAILED CHECK: $line"
            failed=1
        fi
        if echo "$line" | grep -q "on-pull-request" && echo "$line" | grep -q "fail"; then
            warn "FAILED CHECK: $line"
            failed=1
        fi
    done <<< "$final_checks"

    if [ $failed -eq 1 ]; then
        return 1
    fi

    return 0
}

merge_pr() {
    local PR_URL="$1"
    local BRANCH="$2"

    gh pr merge "$PR_URL" --rebase >&2

    git checkout main >&2
    git pull >&2
    git branch -d "$BRANCH" 2>/dev/null || true
}

CREATED_PRS=()
FAILED_PRS=()

for line in "${INPUT_LINES[@]}"; do
    line="$(echo "$line" | xargs)"
    [[ -z "$line" || "$line" == \#* ]] && continue

    PKG_NAME="${line%%==*}"
    VERSION="${line##*==}"
    PKG_FILE="$PACKAGES_DIR/$PKG_NAME.json"
    BRANCH="build/${PKG_NAME}==${VERSION}"

    if [[ ! -f "$PKG_FILE" ]]; then
        warn "$PKG_FILE does not exist, skipping $PKG_NAME"
        continue
    fi

    CURRENT_VERSION="$(jq -r '.version' "$PKG_FILE")"
    if [[ "$CURRENT_VERSION" == "$VERSION" ]]; then
        log "$PKG_NAME is already at version $VERSION, skipping"
        continue
    fi

    log "Processing $PKG_NAME==$VERSION"

    git checkout main >&2
    git pull >&2

    git checkout -b "$BRANCH" >&2

    jq --arg v "$VERSION" '
        .version = $v |
        .ignored_versions = [.ignored_versions[] | select(. != $v)]
    ' "$PKG_FILE" > "${PKG_FILE}.tmp" && mv "${PKG_FILE}.tmp" "$PKG_FILE"

    git add "$PKG_FILE"
    git commit -m "Build ${PKG_NAME}==${VERSION}" >&2

    git push -u origin "$BRANCH" >&2

    sleep 3

    PR_URL="$(gh pr create \
        --title "Build ${PKG_NAME}==${VERSION}" \
        --body "$(cat <<EOF
## Summary
- Build $PKG_NAME at version $VERSION
EOF
)")"

    log "PR created: $PR_URL"

    git checkout main >&2

    if [[ "$ONLY_CREATE_PR" == true ]]; then
        CREATED_PRS+=("$PR_URL")
        continue
    fi

    log "Waiting for checks on $PR_URL..."
    if wait_for_checks "$PR_URL"; then
        log "All checks passed for $PR_URL, merging..."
        merge_pr "$PR_URL" "$BRANCH"
        log "Merged: $PR_URL"
        CREATED_PRS+=("$PR_URL")
    else
        warn "Checks failed for $PR_URL, skipping merge"
        FAILED_PRS+=("$PR_URL")
    fi

done

log "Done. All entries processed."
if [[ ${#CREATED_PRS[@]} -gt 0 ]]; then
    log "Successful: ${#CREATED_PRS[@]}"
fi
if [[ ${#FAILED_PRS[@]} -gt 0 ]]; then
    warn "Failed checks (not merged): ${#FAILED_PRS[@]}"
    for pr in "${FAILED_PRS[@]}"; do
        warn "  $pr"
    done
fi
