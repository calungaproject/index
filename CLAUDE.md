# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is the **Calunga / Trusted Libraries Index** — a Red Hat project that maintains a curated index of Python packages built into trusted wheels from source. It is **not** a Python library or application; it is a package registry management system. The primary artifact is `packages.txt`, which lists pinned package versions to be built and published to Pulp (`packages.redhat.com`).

## Key Concepts

- **packages.txt**: The source of truth — one `package==version` per line, ~300 packages
- **onboarded_packages/**: One JSON file per package with `{"ignored_versions": [...]}` for versions to skip during automated updates
- **Build pipeline**: Tekton/Konflux on OpenShift. Builds wheels from source, runs security scans (Snyk, Coverity, ClamAV, SAST), and pushes OCI artifacts to Quay
- **Automated updates**: GitHub Actions workflow checks PyPI for new versions not yet in Pulp, creates PRs with auto-merge

## Common Operations

### Onboard a new package
```bash
python hack/onboard_package.py <package_name>
```
Creates `onboarded_packages/<name>.json` and appends the latest version to `packages.txt`.

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
hack/identify-packages packages.txt <git_revision> <output_file> <status_file>
```
Diffs `packages.txt` against a prior git revision to determine what needs building.

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
