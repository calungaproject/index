#!/bin/bash
set -euo pipefail

if [[ $# -eq 0 ]]; then
    echo "Usage: $0 <package-spec> [<package-spec> ...]"
    echo ""
    echo "Build from PyPI:"
    echo "  $0 typing_extensions==4.14.0"
    echo "  $0 numpy==2.5.1 scipy==1.15.3"
    echo ""
    echo "Build from git (for packages with sdist_url):"
    echo "  $0 'csaf-tool @ git+https://github.com/anthonyharrison/csaf@0.3.2'"
    exit 1
fi

PACKAGES=("$@")
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIPELINE="${REPO_ROOT}/.tekton/build-pipeline.yaml"

WHEEL_SERVER_URL="https://packages.redhat.com/api/pypi/public-trusted-libraries/main/simple/"

BUILD_TASK_BUNDLE="$(
    < "${PIPELINE}" \
    yq '.spec.tasks[] | select(.name == "build-wheels").taskRef.params[] | select(.name == "bundle") | .value'
)"

BUILDER_IMAGE="$(
    tkn bundle list -o json "${BUILD_TASK_BUNDLE}" | \
    yq -r '.spec.steps[] | select(.name == "build-wheels") | .image'
)"
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
    --package-settings-dir /var/workdir/source/overrides/settings

echo "Collecting build files..."
podman run --rm \
    -v "${WORKDIR}:/var/workdir:Z" \
    "${BUILDER_IMAGE}" \
    collect-build-files /var/workdir/output /var/workdir/artifact

cp -a "${WORKDIR}/artifact"/* "${OUTPUT_DIR}/"

echo ""
echo "Built wheels:"
find "${OUTPUT_DIR}" -name '*.whl' -printf '  %f\n'
