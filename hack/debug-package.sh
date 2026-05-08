#!/bin/bash
set -euo pipefail

NAMESPACE="calunga-tenant"
COMPONENT="calunga-v2-index-main"
KONFLUX_UI="https://konflux-ui.apps.kflux-prd-rh03.nnv1.p1.openshiftapps.com"
GITHUB_REPO="https://github.com/calungaproject/index"
PACKAGES_DIR="onboarded_packages"

usage() {
    cat >&2 <<'USAGE'
Usage: hack/debug-package.sh <package_name>

Traces a package through the full Konflux pipeline:
  commit → on-push build → snapshot → integration tests → release
USAGE
    exit 1
}

log() { echo "==> $*" >&2; }

[ $# -ge 1 ] || usage
PACKAGE="$1"
PKG_FILE="$PACKAGES_DIR/$PACKAGE.json"

if [ ! -f "$PKG_FILE" ]; then
    echo "ERROR: $PKG_FILE does not exist" >&2
    exit 1
fi

if ! oc whoami &>/dev/null; then
    echo "ERROR: not logged in to OpenShift. Run 'oc login' first." >&2
    exit 1
fi

echo "=== Package: $PACKAGE ==="
echo

# -- Commit --
COMMIT_SHA=$(git log -1 --format="%H" -- "$PKG_FILE")
if [ -z "$COMMIT_SHA" ]; then
    echo "-- Commit --"
    echo "  (no commit found for $PKG_FILE)"
    exit 1
fi

COMMIT_TITLE=$(git log -1 --format="%s" "$COMMIT_SHA")
COMMIT_DATE=$(git log -1 --format="%ci" "$COMMIT_SHA")

echo "-- Commit --"
echo "  SHA:   $COMMIT_SHA"
echo "  Title: $COMMIT_TITLE"
echo "  Date:  $COMMIT_DATE"
echo "  URL:   $GITHUB_REPO/commit/$COMMIT_SHA"
echo

# -- Snapshot --
log "Querying snapshots..."
SNAPSHOTS_JSON=$(oc get snapshots -n "$NAMESPACE" \
    -l "appstudio.openshift.io/component=$COMPONENT" \
    -o json 2>/dev/null)

SNAPSHOT_NAME=$(echo "$SNAPSHOTS_JSON" | \
    jq -r --arg sha "$COMMIT_SHA" \
    '.items[] | select(.spec.components[]? | select(.name == "calunga-v2-index-main" and .source.git.revision == $sha)) | .metadata.name' | head -1)

if [ -z "$SNAPSHOT_NAME" ]; then
    echo "-- On-Push Pipeline (BUILD) --"
    log "Querying PipelineRuns by commit SHA..."
    PLR_JSON=$(oc get pipelineruns -n "$NAMESPACE" \
        -l "pipelinesascode.tekton.dev/sha=$COMMIT_SHA" \
        -o json 2>/dev/null)
    PLR_COUNT=$(echo "$PLR_JSON" | jq '.items | length')

    if [ "$PLR_COUNT" -eq 0 ]; then
        echo "  (no PipelineRun found — pipeline may not have triggered or was garbage-collected)"
    else
        echo "$PLR_JSON" | jq -r --arg ui "$KONFLUX_UI" --arg ns "$NAMESPACE" \
            '.items[] | "  Name:   \(.metadata.name)\n  Status: \(.status.conditions[-1].reason // "Unknown")\n  URL:    \($ui)/ns/\($ns)/pipelinerun/\(.metadata.name)"'
    fi
    echo

    echo "-- Snapshot --"
    echo "  (none found for commit $COMMIT_SHA)"
    echo
    echo "-- Integration Tests --"
    echo "  (no snapshot — tests did not run)"
    echo
    echo "-- Release --"
    echo "  (no snapshot — release was not created)"
    exit 0
fi

# -- On-Push Pipeline (from snapshot label) --
SNAPSHOT_JSON=$(echo "$SNAPSHOTS_JSON" | \
    jq --arg name "$SNAPSHOT_NAME" '.items[] | select(.metadata.name == $name)')

BUILD_PLR=$(echo "$SNAPSHOT_JSON" | \
    jq -r '.metadata.labels["appstudio.openshift.io/build-pipelinerun"] // "(unknown)"')

echo "-- On-Push Pipeline (BUILD) --"
echo "  Name:   $BUILD_PLR"

log "Querying build PipelineRun status..."
BUILD_PLR_STATUS=$(oc get pipelinerun "$BUILD_PLR" -n "$NAMESPACE" -o json 2>/dev/null | \
    jq -r '.status.conditions[-1].reason // "Unknown"' 2>/dev/null || echo "(garbage-collected)")
echo "  Status: $BUILD_PLR_STATUS"
echo "  URL:    $KONFLUX_UI/ns/$NAMESPACE/pipelinerun/$BUILD_PLR"
echo

# -- Snapshot details --
SNAPSHOT_TS=$(echo "$SNAPSHOT_JSON" | jq -r '.metadata.creationTimestamp')

TEST_RESULT=$(echo "$SNAPSHOT_JSON" | \
    jq -r '(.status.conditions // [])[] | select(.type == "AppStudioTestSucceeded") | "\(.reason) (\(.status))"' 2>/dev/null || echo "(unknown)")

AUTO_RELEASED_REASON=$(echo "$SNAPSHOT_JSON" | \
    jq -r '(.status.conditions // [])[] | select(.type == "AutoReleased") | .reason' 2>/dev/null || echo "")
AUTO_RELEASED_MSG=$(echo "$SNAPSHOT_JSON" | \
    jq -r '(.status.conditions // [])[] | select(.type == "AutoReleased") | .message' 2>/dev/null || echo "")

echo "-- Snapshot --"
echo "  Name:    $SNAPSHOT_NAME"
echo "  Created: $SNAPSHOT_TS"
echo "  URL:     $KONFLUX_UI/ns/$NAMESPACE/applications/$COMPONENT/snapshots/$SNAPSHOT_NAME"
echo "  Tests:   $TEST_RESULT"
if [ -n "$AUTO_RELEASED_REASON" ]; then
    echo "  AutoReleased: $AUTO_RELEASED_REASON — $AUTO_RELEASED_MSG"
fi
echo

# -- Integration Tests --
TEST_STATUSES=$(echo "$SNAPSHOT_JSON" | \
    jq -r '.metadata.annotations["test.appstudio.openshift.io/status"] // "[]"')

APP_URL="$KONFLUX_UI/ns/$NAMESPACE/applications/$COMPONENT"

echo "-- Integration Tests --"
echo "$TEST_STATUSES" | jq -r --arg url "$APP_URL" \
    '.[] | "  \(.scenario): \(.status) — \(.details // "")\n    \($url)/integrationtests/\(.scenario)"'
echo

# -- Release --
log "Querying releases..."
RELEASES_JSON=$(oc get releases -n "$NAMESPACE" -o json 2>/dev/null)

RELEASE_ARRAY=$(echo "$RELEASES_JSON" | \
    jq --arg snap "$SNAPSHOT_NAME" '[.items[] | select(.spec.snapshot == $snap)]')

RELEASE_COUNT=$(echo "$RELEASE_ARRAY" | jq 'length')

if [ "$RELEASE_COUNT" -eq 0 ]; then
    echo "-- Release --"
    echo "  (no release created for snapshot $SNAPSHOT_NAME)"
else
    echo "-- Release ($RELEASE_COUNT) --"
    echo "$RELEASE_ARRAY" | jq -c '.[]' | while read -r rel; do
        REL_NAME=$(echo "$rel" | jq -r '.metadata.name')
        REL_TS=$(echo "$rel" | jq -r '.metadata.creationTimestamp')
        REL_STATUS=$(echo "$rel" | \
            jq -r '(.status.conditions // [])[] | select(.type == "Released") | "\(.reason) (status=\(.status))"')
        MANAGED_STATUS=$(echo "$rel" | \
            jq -r '(.status.conditions // [])[] | select(.type == "ManagedPipelineProcessed") | .reason')
        MANAGED_MSG=$(echo "$rel" | \
            jq -r '(.status.conditions // [])[] | select(.type == "ManagedPipelineProcessed") | .message // ""')
        MANAGED_PLR=$(echo "$rel" | \
            jq -r '.status.managedProcessing.pipelineRun // "(none)"')

        echo "  Name:      $REL_NAME"
        echo "  Created:   $REL_TS"
        echo "  Released:  $REL_STATUS"
        echo "  Managed Pipeline: $MANAGED_STATUS"
        if [ -n "$MANAGED_MSG" ]; then
            echo "    Message: $MANAGED_MSG"
        fi
        if [ "$MANAGED_PLR" != "(none)" ]; then
            MANAGED_PLR_NS="${MANAGED_PLR%%/*}"
            MANAGED_PLR_NAME="${MANAGED_PLR##*/}"
            echo "  Managed PipelineRun: $MANAGED_PLR"
            echo "    URL: $KONFLUX_UI/ns/$MANAGED_PLR_NS/pipelinerun/$MANAGED_PLR_NAME"
        fi
        echo
    done
fi
