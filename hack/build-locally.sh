#!/bin/bash
set -euo pipefail

BUILDER_IMAGE_OVERRIDE=""
PACKAGES=()
CONSTRAINT_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --builder-image)
            if [[ $# -lt 2 ]]; then
                echo "Error: --builder-image requires an argument" >&2
                exit 1
            fi
            BUILDER_IMAGE_OVERRIDE="$2"
            shift 2
            ;;
        -c|--constraint)
            if [[ $# -lt 2 ]]; then
                echo "Error: $1 requires an argument" >&2
                exit 1
            fi
            # fromager declares --constraints-file as a plain string, so a
            # second -c overwrites the first and the earlier file is dropped
            # without a word. hack/identify-constraints refuses that in CI, so
            # refuse it here too: the point of this script is to reproduce what
            # CI does, and quietly applying one of two files does the opposite.
            if [[ ${#CONSTRAINT_ARGS[@]} -gt 0 ]]; then
                echo "Error: only one constraints file can be applied." >&2
                echo "fromager reads just the last -c, so the others would be" >&2
                echo "silently ignored and this build would not match CI." >&2
                echo "Reconcile the pins into a single file." >&2
                exit 1
            fi
            # Same transform the build-wheels Tekton step applies, so a path
            # that works here works in overrides/constraints/ unchanged.
            CONSTRAINT_ARGS+=(-c "/var/workdir/source/$2")
            shift 2
            ;;
        *)
            PACKAGES+=("$1")
            shift
            ;;
    esac
done

if [[ ${#PACKAGES[@]} -eq 0 ]]; then
    echo "Usage: $0 [--builder-image <image>] [-c <constraints-file>] <package-spec> [<package-spec> ...]"
    echo ""
    echo "Build from PyPI:"
    echo "  $0 typing_extensions==4.14.0"
    echo "  $0 numpy==2.5.1 scipy==1.15.3"
    echo ""
    echo "Build from git (for packages with sdist_url):"
    echo "  $0 'csaf-tool @ git+https://github.com/anthonyharrison/csaf@0.3.2'"
    echo ""
    echo "Build with constraints (reproduces what CI applies from overrides/constraints/):"
    echo "  $0 -c overrides/constraints/google-adk-1.36.2.txt google-adk==1.36.2"
    echo ""
    echo "Options:"
    echo "  --builder-image <image>  Use a custom builder image instead of the one from the pipeline"
    echo "  -c, --constraint <path>  Repo-relative constraints file (at most one)"
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIPELINE="${REPO_ROOT}/.tekton/build-pipeline.yaml"

WHEEL_SERVER_URL="https://packages.redhat.com/api/pypi/public-trusted-libraries/main/simple/"

if [[ -n "${BUILDER_IMAGE_OVERRIDE}" ]]; then
    BUILDER_IMAGE="${BUILDER_IMAGE_OVERRIDE}"
else
    BUILD_TASK_BUNDLE="$(
        < "${PIPELINE}" \
        yq '.spec.tasks[] | select(.name == "build-wheels").taskRef.params[] | select(.name == "bundle") | .value'
    )"

    BUILDER_IMAGE="$(
        tkn bundle list -o json "${BUILD_TASK_BUNDLE}" | \
        yq -r '.spec.steps[] | select(.name == "build-wheels") | .image'
    )"
fi
OUTPUT_DIR="${REPO_ROOT}/output"
WORKDIR=$(mktemp -d)
trap 'rm -rf "${WORKDIR}"' EXIT

rm -rf "${OUTPUT_DIR:?}"/*
mkdir -p "${OUTPUT_DIR}"

echo "Pulling builder image..."
podman pull "${BUILDER_IMAGE}"

echo "Building wheels: ${PACKAGES[*]}"
podman run -it --rm \
    -v "${WORKDIR}:/var/workdir:Z" \
    -v "${REPO_ROOT}:/var/workdir/source:ro,Z" \
    -w /var/workdir \
    "${BUILDER_IMAGE}" \
    build-wheels "${PACKAGES[@]}" --cache-wheel-server-url "${WHEEL_SERVER_URL}" \
    --package-settings-dir /var/workdir/source/overrides/settings \
    ${CONSTRAINT_ARGS[@]+"${CONSTRAINT_ARGS[@]}"}

echo "Collecting build files..."
podman run --rm \
    -v "${WORKDIR}:/var/workdir:Z" \
    "${BUILDER_IMAGE}" \
    collect-build-files /var/workdir/output /var/workdir/artifact

cp -a "${WORKDIR}/artifact"/* "${OUTPUT_DIR}/"

echo ""
echo "Built wheels:"
find "${OUTPUT_DIR}" -name '*.whl' -printf '  %f\n'
