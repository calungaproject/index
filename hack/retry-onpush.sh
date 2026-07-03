#!/bin/bash
set -euo pipefail

NAMESPACE="calunga-tenant"
PIPELINE_YAML=".tekton/build-pipeline.yaml"
GIT_AUTH_SECRET="git-auth-dummy"

usage() {
    cat >&2 <<'USAGE'
Usage: hack/retry-onpush.sh <pipelinerun-name>

Retries a failed on-push PipelineRun for its original commit.

Do NOT use the Konflux UI "Rerun" button — it re-resolves {{revision}}
against the current HEAD of main, so identify-packages diffs the wrong
commit pair and builds the wrong (or no) packages.

This script:
  1. Fetches the original PipelineRun (live cluster or kubearchive)
  2. Shows the commit for verification
  3. Combines archived params with the CURRENT pipeline spec
  4. Creates a new PipelineRun targeting the original commit
USAGE
    exit 1
}

log() { echo "==> $*" >&2; }

[ $# -ge 1 ] || usage

log "Checking cluster login..."
oc whoami >/dev/null 2>&1 || { echo "ERROR: not logged in to the cluster. Run 'oc login' first." >&2; exit 1; }

FAILED_PLR="$1"

ARCHIVED_PLR=$(mktemp /tmp/archived-plr.XXXXXX.json)
PIPELINE_SPEC=$(mktemp /tmp/current-pipeline-spec.XXXXXX.json)
RETRY_PLR=$(mktemp /tmp/retry-plr.XXXXXX.json)
trap 'rm -f "$ARCHIVED_PLR" "$PIPELINE_SPEC" "$RETRY_PLR"' EXIT

log "Fetching PipelineRun $FAILED_PLR"
if ! oc get pipelinerun "$FAILED_PLR" -n "$NAMESPACE" -o json > "$ARCHIVED_PLR" 2>/dev/null; then
    log "Not on live cluster, trying kubearchive..."
    kubectl ka get pipelineruns.v1.tekton.dev "$FAILED_PLR" -n "$NAMESPACE" -o json \
        | jq '.items[0]' > "$ARCHIVED_PLR"
fi

REVISION=$(jq -r '.spec.params[] | select(.name == "revision") | .value' "$ARCHIVED_PLR")
SHA_TITLE=$(jq -r '.metadata.annotations["pipelinesascode.tekton.dev/sha-title"] // "N/A"' "$ARCHIVED_PLR")
log "Commit: $REVISION"
log "Title:  $SHA_TITLE"

if [ ! -f "$PIPELINE_YAML" ]; then
    echo "ERROR: $PIPELINE_YAML not found — run from the repo root" >&2
    exit 1
fi

log "Reading current pipeline spec from $PIPELINE_YAML"
python3 -c "
import yaml, json, sys
with open('$PIPELINE_YAML') as f:
    pipeline = yaml.safe_load(f)
json.dump(pipeline['spec'], sys.stdout)
" > "$PIPELINE_SPEC"

log "Building retry PipelineRun"
jq -n \
  --slurpfile archived "$ARCHIVED_PLR" \
  --slurpfile pipelineSpec "$PIPELINE_SPEC" \
  --arg gitAuthSecret "$GIT_AUTH_SECRET" \
'{
  apiVersion: $archived[0].apiVersion,
  kind: $archived[0].kind,
  metadata: {
    generateName: "calunga-v2-index-main-on-push-retry-",
    namespace: $archived[0].metadata.namespace,
    annotations: (
      ($archived[0].metadata.annotations | with_entries(
        select(.key | test("^(build\\.appstudio|test\\.appstudio|pipelinesascode\\.tekton\\.dev/(cancel-in-progress|max-keep-runs|on-cel-expression|original-prname|repository|sha|sha-title|sha-url|event-type|branch|source-branch|source-repo-url|repo-url|url-org|url-repository|git-provider|installation-id))"))
      ))
      + {"test.appstudio.openshift.io/ignore-supersession": "true"}
    ),
    labels: (
      $archived[0].metadata.labels | with_entries(
        select(.key | test("^(appstudio|pipelines\\.appstudio|pipelinesascode\\.tekton\\.dev/(original-prname|repository|sha|event-type|url-org|url-repository|cancel-in-progress))|tekton\\.dev/pipeline"))
      )
    )
  },
  spec: {
    params: $archived[0].spec.params,
    pipelineSpec: $pipelineSpec[0],
    taskRunTemplate: $archived[0].spec.taskRunTemplate,
    taskRunSpecs: $archived[0].spec.taskRunSpecs,
    workspaces: [
      {
        name: "git-auth",
        secret: {
          secretName: $gitAuthSecret
        }
      }
    ]
  }
}' > "$RETRY_PLR"

log "Creating retry PipelineRun"
oc create -f "$RETRY_PLR" -n "$NAMESPACE"

log "Verifying..."
oc get pipelinerun -n "$NAMESPACE" \
  -l "pipelinesascode.tekton.dev/sha=$REVISION" \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.conditions[0].reason}{"\n"}{end}'
