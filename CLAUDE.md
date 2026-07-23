# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is the **Calunga / Trusted Libraries Index** — a Red Hat project that maintains a curated index of Python packages built into trusted wheels from source. It is **not** a Python library or application; it is a package registry management system. The primary artifact is the `onboarded_packages/` directory, where each JSON file specifies the latest version to be built and published to Pulp (`packages.redhat.com`).

## Key Concepts

- **onboarded_packages/**: The source of truth — one JSON file per package with `{"version": "...", "ignored_versions": [...]}`. The `version` field specifies the latest version to build; `ignored_versions` lists versions to skip during automated updates. An optional `build_extra` field (list of bare package names) declares undeclared dependencies that must be built alongside the package — `identify-packages` resolves each to its current version from the corresponding JSON file. Every entry in `build_extra` must have a matching onboarded package JSON or the build will fail. An optional `sdist_url` field (e.g. `"git+https://github.com/org/repo.git"`) directs the build to fetch the source from a git URL instead of PyPI — `identify-packages` produces a PEP 440 URL requirement (`pkg @ url@version`) when this field is present. An optional `git_tag_template` field (e.g. `"v{version}"`) controls how the version maps to a git tag when `sdist_url` is set — `{version}` is replaced with the package version; defaults to `{version}` if absent.
- **Build pipeline**: Tekton/Konflux on OpenShift. Builds wheels from source, runs security scans (Snyk, Coverity, ClamAV, SAST), and pushes OCI artifacts to Quay
- **Automated updates**: GitHub Actions workflow checks PyPI for new versions not yet in Pulp, creates PRs with auto-merge

## Common Operations

### Onboard a new package
```bash
hack/onboard.sh <package_name>
```
Full lifecycle: creates `onboarded_packages/<name>.json`, branches, commits, pushes, opens a PR, waits for CI, merges, and cleans up. Subcommands (`create`, `wait`, `merge`) are available for batch workflows — see `hack/onboard.sh --help`.

### Build a package locally
```bash
# From PyPI
hack/build-locally.sh "typing_extensions==4.14.0"

# From git (for packages with sdist_url)
hack/build-locally.sh 'csaf-tool @ git+https://github.com/anthonyharrison/csaf@0.3.2'

# With a custom builder image (e.g. from a PR build)
hack/build-locally.sh --builder-image quay.io/redhat-user-workloads/calunga-tenant/plumbing-builder@sha256:abc123 "pyarrow==25.0.0"
```
Runs the same builder image used in CI via podman (or a custom one via `--builder-image`). Built wheels are saved to `output/`. Requires `podman`, `yq`, and `tkn`.

### Check for available updates
```bash
python hack/check-for-updates.py
```
Requires `SERVICE_ACCOUNT_USERNAME` and `SERVICE_ACCOUNT_PASSWORD` env vars. Uses aiohttp to async-compare PyPI vs Pulp versions. Outputs JSON of packages needing builds.

### Update a package version
```bash
hack/replace-package "<package>==<version>"
```

### Identify new/changed packages (used by CI)
```bash
hack/identify-packages <git_revision> <output_file> <status_file>
```
Compares `onboarded_packages/` JSON files against a prior git revision to determine what needs building.

### Generate list of available packages in Pulp
```bash
python hack/generate-available-packages.py
```
Requires Pulp credentials and configuration env vars.

## CI/CD Architecture

- **Tekton pipelines** in `.tekton/`: triggered on push to main and on PRs
- **PR trigger** (`.tekton/calunga-v2-index-main-pull-request.yaml`): compares against `origin/main`
- **Push trigger** (`.tekton/calunga-v2-index-main-push.yaml`): compares against `HEAD^`
- **Build task**: `build-python-wheels-oci-ta` — builds wheels from source with 20Gi memory limit
- **Enterprise Contract**: policy config in `konflux/ecp.yaml`
- **GitHub Actions** (`.github/workflows/get_new_package_versions.yml`): periodic PyPI update checker, creates automated PRs

## Debugging Pipeline Failures

When debugging Konflux pipeline failures (wheel-check failures, build errors, release issues), always consult `.claude/agents/debug-package.md` first. It contains the full diagnostic procedure. Always use `kubectl ka get` (not `oc get`) for PipelineRuns, TaskRuns, and pods — the kubearchive plugin transparently queries both the live cluster and the archive, so there is no need to check liveness first.

## Commit Message Convention

Automated builds follow: `Automatic build <package>==<version>`

## Git Commit Attribution

Use `Assisted-by` as the commit trailer (not `Co-Authored-By`).

## Platform Support

- Python 3.12 (primary), Python 3.13 (planned)
- x86_64 / manylinux_2_28 (aarch64 planned)
- Tested on: UBI9, UBI10, Fedora, Ubuntu 24.04
