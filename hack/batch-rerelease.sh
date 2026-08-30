#!/usr/bin/env bash
#
# Create Release CRs for a list of snapshots, in batches.
# Waits for each batch to reach a terminal state before starting the next.
#
# Prerequisites:
#   oc login https://api.kflux-prd-rh03.nnv1.p1.openshiftapps.com:6443 --token=<token>
#
# Usage:
#   ./hack/batch-rerelease.sh [--input FILE] [--batch-size N] [--max-wait N]
#       [--namespace NS] [--release-plan NAME] [--dry-run] [--log FILE]
#
# Defaults:
#   --input        snapshots.txt
#   --batch-size   5
#   --max-wait     40       (40 * 30s = 20 minutes per batch)
#   --namespace    calunga-tenant
#   --release-plan calunga
#   --log          rerelease-results.log

set -euo pipefail

INPUT="snapshots.txt"
BATCH_SIZE=5
MAX_WAIT=40
NAMESPACE="calunga-tenant"
RELEASE_PLAN="calunga"
DRY_RUN=false
LOG_FILE="rerelease-results.log"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --input)        INPUT="$2"; shift 2 ;;
        --batch-size)   BATCH_SIZE="$2"; shift 2 ;;
        --max-wait)     MAX_WAIT="$2"; shift 2 ;;
        --namespace)    NAMESPACE="$2"; shift 2 ;;
        --release-plan) RELEASE_PLAN="$2"; shift 2 ;;
        --dry-run)      DRY_RUN=true; shift ;;
        --log)          LOG_FILE="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,/^$/p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [ ! -f "$INPUT" ]; then
    echo "ERROR: Input file not found: $INPUT" >&2
    exit 1
fi

oc whoami &>/dev/null || {
    echo "ERROR: Not logged into the cluster." >&2
    echo "Run: oc login https://api.kflux-prd-rh03.nnv1.p1.openshiftapps.com:6443 --token=<token>" >&2
    exit 1
}

mapfile -t SNAPSHOTS < "$INPUT"
TOTAL=${#SNAPSHOTS[@]}

echo "Batch Re-release"
echo "  Input:        $INPUT ($TOTAL snapshots)"
echo "  Namespace:    $NAMESPACE"
echo "  Release plan: $RELEASE_PLAN"
echo "  Batch size:   $BATCH_SIZE"
echo "  Max wait:     $((MAX_WAIT * 30))s per batch"
echo "  Dry run:      $DRY_RUN"
echo "  Log:          $LOG_FILE"
echo

if [ "$DRY_RUN" = true ]; then
    echo "[DRY RUN] Would create $TOTAL releases. First 5:"
    for ((i=0; i<5 && i<TOTAL; i++)); do
        echo "  Release for snapshot: ${SNAPSHOTS[$i]}"
    done
    echo "  ..."
    exit 0
fi

: > "$LOG_FILE"

log() {
    local msg="[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"
    echo "$msg"
    echo "$msg" >> "$LOG_FILE"
}

SUCCEEDED=0
FAILED=0
TIMED_OUT=0

wait_for_releases() {
    local names=("$@")
    local attempts=0
    while true; do
        local pending=0
        for name in "${names[@]}"; do
            local rel_reason
            rel_reason=$(oc get release "$name" -n "$NAMESPACE" -o json 2>/dev/null | \
                jq -r '(.status.conditions // [])[] | select(.type == "Released") | .reason') || true
            if [ "$rel_reason" = "Progressing" ] || [ -z "$rel_reason" ]; then
                pending=$((pending + 1))
            fi
        done
        if [ "$pending" -eq 0 ]; then break; fi
        attempts=$((attempts + 1))
        if [ "$attempts" -ge "$MAX_WAIT" ]; then
            log "  Timed out after $((MAX_WAIT * 30))s, $pending still progressing"
            break
        fi
        echo "  Waiting... $pending/${#names[@]} still progressing (attempt $attempts/$MAX_WAIT)"
        sleep 30
    done

    for name in "${names[@]}"; do
        local rel_json
        rel_json=$(oc get release "$name" -n "$NAMESPACE" -o json 2>/dev/null) || true
        local rel_result
        rel_result=$(echo "$rel_json" | \
            jq -r '(.status.conditions // [])[] | select(.type == "Released") | .reason') || true
        local snapshot
        snapshot=$(echo "$rel_json" | jq -r '.spec.snapshot') || true

        case "$rel_result" in
            Succeeded) SUCCEEDED=$((SUCCEEDED + 1)) ;;
            Failed)    FAILED=$((FAILED + 1)) ;;
            *)         TIMED_OUT=$((TIMED_OUT + 1)) ;;
        esac

        log "  $name ($snapshot): $rel_result"
    done
}

log "Starting batch re-release of $TOTAL snapshots"

idx=0
batch_num=0
while [ $idx -lt $TOTAL ]; do
    batch_num=$((batch_num + 1))
    end=$((idx + BATCH_SIZE))
    if [ $end -gt $TOTAL ]; then end=$TOTAL; fi

    log "--- Batch $batch_num: snapshots $((idx+1))-${end} of $TOTAL ---"

    batch_names=()
    for ((i=idx; i<end; i++)); do
        snapshot="${SNAPSHOTS[$i]}"
        rel_name=$(oc create -f - -o jsonpath='{.metadata.name}' <<EOF
apiVersion: appstudio.redhat.com/v1alpha1
kind: Release
metadata:
  generateName: calunga-retry-
  namespace: $NAMESPACE
spec:
  releasePlan: $RELEASE_PLAN
  snapshot: $snapshot
  gracePeriodDays: 7
EOF
) || {
            log "  ERROR: Failed to create release for $snapshot"
            FAILED=$((FAILED + 1))
            continue
        }
        log "  Created $rel_name for $snapshot"
        batch_names+=("$rel_name")
    done

    if [ ${#batch_names[@]} -gt 0 ]; then
        wait_for_releases "${batch_names[@]}"
    fi

    idx=$end

    log "  Progress: $idx/$TOTAL done (succeeded=$SUCCEEDED, failed=$FAILED, timed_out=$TIMED_OUT)"
    echo
done

log "=== COMPLETE ==="
log "Total: $TOTAL | Succeeded: $SUCCEEDED | Failed: $FAILED | Timed out: $TIMED_OUT"
log "Full log: $LOG_FILE"
