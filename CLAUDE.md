# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is the **Calunga / Trusted Libraries Index** — a Red Hat project that maintains a curated index of Python packages built into trusted wheels from source. It is **not** a Python library or application; it is a package registry management system. The primary artifact is the `onboarded_packages/` directory, where each JSON file specifies the latest version to be built and published to Pulp (`packages.redhat.com`).

## Key Concepts

- **onboarded_packages/**: The source of truth — one JSON file per package with `{"version": "...", "ignored_versions": [...]}`. The `version` field specifies the latest version to build; `ignored_versions` lists versions to skip during automated updates.
- **Build pipeline**: Tekton/Konflux on OpenShift. Builds wheels from source, runs security scans (Snyk, Coverity, ClamAV, SAST), and pushes OCI artifacts to Quay
- **Automated updates**: GitHub Actions workflow checks PyPI for new versions not yet in Pulp, creates PRs with auto-merge

## Common Operations

### Onboard a new package
```bash
hack/onboard.sh <package_name>
```
Full lifecycle: creates `onboarded_packages/<name>.json`, branches, commits, pushes, opens a PR, waits for CI, merges, and cleans up. Subcommands (`create`, `wait`, `merge`) are available for batch workflows — see `hack/onboard.sh --help`.

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

## Commit Message Convention

Automated builds follow: `Automatic build <package>==<version>`

## Platform Support

- Python 3.12 (primary), Python 3.13 (planned)
- x86_64 / manylinux_2_28 (aarch64 planned)
- Tested on: UBI9, UBI10, Fedora, Ubuntu 24.04
