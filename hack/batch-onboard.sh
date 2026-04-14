#!/bin/bash
set -euo pipefail

BATCH_SIZE=5
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ONBOARD="$SCRIPT_DIR/onboard.sh"

usage() {
    cat >&2 <<USAGE
Usage: $(basename "$0") <packages-file>

Onboard packages listed in <packages-file> (one per line) in batches of $BATCH_SIZE.
For each batch: creates PRs sequentially, then runs wait+merge in parallel.
Lines starting with # and blank lines are ignored.
USAGE
    exit 1
}

log() { printf '[batch] %s\n' "$*" >&2; }

[[ $# -eq 1 ]] || usage
INPUT_FILE="$1"
[[ -f "$INPUT_FILE" ]] || { echo "ERROR: File not found: $INPUT_FILE" >&2; exit 1; }

mapfile -t PACKAGES < <(sed 's/#.*//' "$INPUT_FILE" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | grep -v '^$')

if [[ ${#PACKAGES[@]} -eq 0 ]]; then
    log "No packages found in $INPUT_FILE"
    exit 0
fi

TOTAL_BATCHES=$(( (${#PACKAGES[@]} + BATCH_SIZE - 1) / BATCH_SIZE ))
log "Found ${#PACKAGES[@]} package(s) to onboard in $TOTAL_BATCHES batch(es)"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

TOTAL_OK=0
TOTAL_FAIL=0
FAILED_PACKAGES=()

for ((i = 0; i < ${#PACKAGES[@]}; i += BATCH_SIZE)); do
    batch=("${PACKAGES[@]:i:BATCH_SIZE}")
    batch_num=$(( i / BATCH_SIZE + 1 ))
    log "=== Batch $batch_num/$TOTAL_BATCHES (${#batch[@]} packages): ${batch[*]} ==="

    # Phase 1: Create PRs sequentially (git operations require serialization)
    declare -A PR_URLS=()
    create_failed=()
    for pkg in "${batch[@]}"; do
        log "Creating PR for $pkg..."
        if pr_url=$("$ONBOARD" create "$pkg"); then
            PR_URLS["$pkg"]="$pr_url"
            log "Created: $pkg -> $pr_url"
        else
            log "FAILED to create PR for $pkg"
            create_failed+=("$pkg")
        fi
    done

    if [[ ${#create_failed[@]} -gt 0 ]]; then
        TOTAL_FAIL=$(( TOTAL_FAIL + ${#create_failed[@]} ))
        FAILED_PACKAGES+=("${create_failed[@]}")
    fi

    if [[ ${#PR_URLS[@]} -eq 0 ]]; then
        log "No PRs to process in this batch, moving on"
        continue
    fi

    # Phase 2: Wait + merge in parallel
    # flock serializes merges to prevent concurrent git operations
    MERGE_LOCK="$TMPDIR/merge.lock"
    touch "$MERGE_LOCK"

    for pkg in "${!PR_URLS[@]}"; do
        pr_url="${PR_URLS[$pkg]}"
        (
            log "Waiting for checks: $pkg ($pr_url)"
            if "$ONBOARD" wait "$pr_url"; then
                log "Checks passed for $pkg, merging..."
                flock "$MERGE_LOCK" "$ONBOARD" merge "$pr_url"
                echo "ok" > "$TMPDIR/$pkg.status"
            else
                log "Checks FAILED for $pkg — PR left open: $pr_url"
                echo "fail" > "$TMPDIR/$pkg.status"
            fi
        ) &
    done

    wait

    # Tally results for this batch
    for pkg in "${!PR_URLS[@]}"; do
        status="$(cat "$TMPDIR/$pkg.status" 2>/dev/null || echo fail)"
        if [[ "$status" == "ok" ]]; then
            TOTAL_OK=$(( TOTAL_OK + 1 ))
        else
            TOTAL_FAIL=$(( TOTAL_FAIL + 1 ))
            FAILED_PACKAGES+=("$pkg")
        fi
        rm -f "$TMPDIR/$pkg.status"
    done

    unset PR_URLS
    log "=== Batch $batch_num complete ==="
done

log ""
log "==============================="
log "  Onboarding complete"
log "  Succeeded: $TOTAL_OK"
log "  Failed:    $TOTAL_FAIL"
if [[ ${#FAILED_PACKAGES[@]} -gt 0 ]]; then
    log "  Failed packages:"
    for pkg in "${FAILED_PACKAGES[@]}"; do
        log "    - $pkg"
    done
fi
log "==============================="

[[ $TOTAL_FAIL -eq 0 ]]
