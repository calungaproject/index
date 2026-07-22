#!/usr/bin/env bash
#
# Extract snapshot names from archived Releases (via KubeArchive) created
# within a date range. Produces a text file (one snapshot per line) that
# can be fed into a batch re-release script.
#
# Prerequisites:
#   oc login https://api.kflux-prd-rh03.nnv1.p1.openshiftapps.com:6443 --token=<token>
#
# Usage:
#   ./hack/extract-snapshots-for-rerelease.sh [--start DATE] [--end DATE] \
#       [--namespace NS] [--output FILE] [--released-only]
#
# Defaults:
#   --start       2026-05-01
#   --end         2026-06-18
#   --namespace   calunga-tenant
#   --output      snapshots.txt
#   --released-only  (off by default; pass flag to keep only Succeeded releases)

set -euo pipefail

KA_HOST="https://kubearchive-api-server-product-kubearchive.apps.kflux-prd-rh03.nnv1.p1.openshiftapps.com"
START_DATE="2026-05-01"
END_DATE="2026-06-18"
NAMESPACE="calunga-tenant"
OUTPUT="snapshots.txt"
RELEASED_ONLY=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --start)       START_DATE="$2"; shift 2 ;;
        --end)         END_DATE="$2"; shift 2 ;;
        --namespace)   NAMESPACE="$2"; shift 2 ;;
        --output)      OUTPUT="$2"; shift 2 ;;
        --released-only) RELEASED_ONLY=true; shift ;;
        -h|--help)
            sed -n '2,/^$/p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

START_TS="${START_DATE}T00:00:00Z"
END_TS="${END_DATE}T23:59:59Z"

TOKEN=$(oc whoami -t 2>/dev/null) || {
    echo "ERROR: Not logged into the cluster." >&2
    echo "Run: oc login https://api.kflux-prd-rh03.nnv1.p1.openshiftapps.com:6443 --token=<token>" >&2
    exit 1
}

echo "Fetching archived releases from KubeArchive"
echo "  Namespace:  $NAMESPACE"
echo "  Date range: $START_DATE to $END_DATE"
echo "  Host:       $KA_HOST"
echo

USE_KUBECTL_KA=false
if command -v kubectl &>/dev/null && kubectl ka --help &>/dev/null 2>&1; then
    USE_KUBECTL_KA=true
fi

if [ "$USE_KUBECTL_KA" = true ]; then
    echo "Using kubectl ka plugin..."
    RELEASES_JSON=$(kubectl ka get releases.v1alpha1.appstudio.redhat.com \
        -n "$NAMESPACE" \
        --after "${START_TS}" \
        --before "${END_TS}" \
        -o json 2>/dev/null)
else
    echo "Using KubeArchive REST API (curl)..."
    API_URL="${KA_HOST}/apis/appstudio.redhat.com/v1alpha1/namespaces/${NAMESPACE}/releases"

    RELEASES_JSON=""
    CONTINUE=""
    PAGE=0

    while true; do
        PAGE=$((PAGE + 1))
        URL="${API_URL}?limit=500&after=${START_TS}&before=${END_TS}"
        if [ -n "$CONTINUE" ]; then
            ENCODED_CONTINUE=$(python3 -c "import urllib.parse; print(urllib.parse.quote('${CONTINUE}', safe=''))")
            URL="${URL}&continue=${ENCODED_CONTINUE}"
        fi

        echo "  Fetching page ${PAGE}..."
        RESPONSE=$(curl -sf -H "Authorization: Bearer ${TOKEN}" "$URL") || {
            echo "ERROR: KubeArchive API request failed." >&2
            echo "Check that you're logged into the correct cluster and have access to namespace ${NAMESPACE}." >&2
            exit 1
        }

        if [ -z "$RELEASES_JSON" ]; then
            RELEASES_JSON="$RESPONSE"
        else
            RELEASES_JSON=$(jq -s '
                {
                    apiVersion: .[0].apiVersion,
                    kind: .[0].kind,
                    items: ([.[].items] | add)
                }' <(echo "$RELEASES_JSON") <(echo "$RESPONSE"))
        fi

        CONTINUE=$(echo "$RESPONSE" | jq -r '.metadata.continue // empty')
        if [ -z "$CONTINUE" ]; then
            break
        fi
    done
fi

TOTAL_FETCHED=$(echo "$RELEASES_JSON" | jq '.items | length')
echo "  Fetched $TOTAL_FETCHED total releases from archive."
echo

FILTER_EXPR='
    .items[]
    | select(.metadata.creationTimestamp >= $start
         and .metadata.creationTimestamp <= $end)
'

if [ "$RELEASED_ONLY" = true ]; then
    FILTER_EXPR="$FILTER_EXPR"'
    | select(
        (.status.conditions // [])[]
        | select(.type == "Released")
        | .reason == "Succeeded"
      )
'
fi

QUERY="[ $FILTER_EXPR ]"

FILTERED=$(echo "$RELEASES_JSON" | \
    jq --arg start "$START_TS" --arg end "$END_TS" "$QUERY")

TOTAL=$(echo "$FILTERED" | jq 'length')
echo "Found $TOTAL releases in date range ($START_DATE to $END_DATE)."

if [ "$TOTAL" -eq 0 ]; then
    echo "No releases found. Nothing to write."
    exit 0
fi

SNAPSHOTS=$(echo "$FILTERED" | \
    jq -r '[ .[] | {
        snapshot: .spec.snapshot,
        release: .metadata.name,
        created: .metadata.creationTimestamp,
        status: (
            [(.status.conditions // [])[] | select(.type == "Released") | .reason]
            | first // "Unknown"
        )
    } ] | sort_by(.created) | .[]
    | "\(.created)\t\(.release)\t\(.snapshot)\t\(.status)"')

echo
printf "%-24s %-40s %-45s %s\n" "DATE" "RELEASE" "SNAPSHOT" "STATUS"
printf "%-24s %-40s %-45s %s\n" "----" "-------" "--------" "------"
echo "$SNAPSHOTS" | while IFS=$'\t' read -r created release snapshot status; do
    printf "%-24s %-40s %-45s %s\n" "$created" "$release" "$snapshot" "$status"
done

echo "$FILTERED" | jq -r '[.[] | .spec.snapshot] | unique | .[]' > "$OUTPUT"

UNIQUE_COUNT=$(wc -l < "$OUTPUT")
echo
echo "Wrote $UNIQUE_COUNT unique snapshot names to $OUTPUT"
