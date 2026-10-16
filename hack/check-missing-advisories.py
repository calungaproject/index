#!/usr/bin/env python3
"""
Check which Pulp package@version pairs are missing advisories in calunga-tenant.

Every package version released to Pulp (packages.redhat.com) should have a
corresponding advisory in the releng advisory GitLab repos under calunga-tenant.
This script finds gaps by comparing pulp_pkgs.json against the advisory repos.

Advisory repos checked (calunga-tenant/ subtree only):
  - https://gitlab.cee.redhat.com/releng/advisories/
  - https://gitlab.cee.redhat.com/releng/advisories-poc/

Both repos are cloned to /tmp/ on first run and reused on subsequent runs.
Pass --update to git-pull both repos before checking.

Package name comparison uses PEP 503 normalization (lowercase, [-_.] → -),
so e.g. "absolufy_imports" in pulp_pkgs.json matches "absolufy-imports" in
advisory PURLs.

Usage examples:
  # Basic check (clones advisory repos if not already present)
  python hack/check-missing-advisories.py

  # Update advisory repos then check
  python hack/check-missing-advisories.py --update

  # JSON output for piping into other tools
  python hack/check-missing-advisories.py --json | jq '.total_missing'

  # Use a freshly generated pulp_pkgs.json
  python hack/generate-available-packages.py 2>/dev/null > pulp_pkgs.json
  python hack/check-missing-advisories.py --update
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ADVISORIES_URL = "https://gitlab.cee.redhat.com/releng/advisories/"
ADVISORIES_POC_URL = "https://gitlab.cee.redhat.com/releng/advisories-poc/"
DEFAULT_ADVISORIES_DIR = Path("/tmp/advisories")
DEFAULT_ADVISORIES_POC_DIR = Path("/tmp/advisories-poc")
PURL_RE = re.compile(r"pkg:pypi/([^@]+)@([^?&\s]+)")


def normalize(name):
    """PEP 503 normalization: lowercase, collapse [-_.] to -."""
    return re.sub(r"[-_.]+", "-", name).lower()


def ensure_repo(url, dest, update=False):
    if dest.exists():
        if update:
            print(f"Updating {dest}...", file=sys.stderr)
            subprocess.run(
                ["git", "-C", str(dest), "fetch", "--depth=1", "origin"],
                check=True, stdout=sys.stderr, stderr=sys.stderr,
            )
            subprocess.run(
                ["git", "-C", str(dest), "reset", "--hard", "origin/HEAD"],
                check=True, stdout=sys.stderr, stderr=sys.stderr,
            )
    else:
        print(f"Cloning {url} -> {dest}...", file=sys.stderr)
        subprocess.run(
            ["git", "clone", "--depth=1", url, str(dest)],
            check=True, stdout=sys.stderr, stderr=sys.stderr,
        )


def load_pulp_pairs(path):
    data = json.loads(Path(path).read_text())
    pairs = set()
    for pkg, info in data["packages"].items():
        norm = normalize(pkg)
        for ver in info["versions"]:
            pairs.add((norm, ver))
    return pairs


def load_advisory_pairs(*repo_dirs):
    pairs = set()
    for repo_dir in repo_dirs:
        calunga = Path(repo_dir) / "data" / "advisories" / "calunga-tenant"
        if not calunga.exists():
            continue
        for f in calunga.rglob("advisory.yaml"):
            for m in PURL_RE.finditer(f.read_text()):
                pairs.add((normalize(m.group(1)), m.group(2)))
    return pairs


def main():
    ap = argparse.ArgumentParser(
        description="Find Pulp package@version pairs with no advisory in calunga-tenant.",
        epilog=(
            "JSON output schema (--json):\n"
            "  {\n"
            '    "missing": ["pkg@ver", ...],\n'
            '    "total_pulp": <int>,\n'
            '    "total_covered": <int>,\n'
            '    "total_missing": <int>\n'
            "  }\n\n"
            "Regenerate pulp_pkgs.json before running to get current Pulp state:\n"
            "  python hack/generate-available-packages.py 2>/dev/null > pulp_pkgs.json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--pulp-pkgs",
        default="pulp_pkgs.json",
        metavar="PATH",
        help="Path to pulp_pkgs.json snapshot of Pulp contents (default: %(default)s)",
    )
    ap.add_argument(
        "--advisories-dir",
        default=DEFAULT_ADVISORIES_DIR,
        type=Path,
        metavar="DIR",
        help="Local clone of the main advisories repo (default: %(default)s)",
    )
    ap.add_argument(
        "--advisories-poc-dir",
        default=DEFAULT_ADVISORIES_POC_DIR,
        type=Path,
        metavar="DIR",
        help="Local clone of the advisories-poc repo (default: %(default)s)",
    )
    ap.add_argument(
        "--update",
        action="store_true",
        help="Fetch latest commits in both advisory repos before checking",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        dest="json_out",
        help="Emit results as JSON instead of plain text",
    )
    args = ap.parse_args()

    ensure_repo(ADVISORIES_URL, args.advisories_dir, args.update)
    ensure_repo(ADVISORIES_POC_URL, args.advisories_poc_dir, args.update)

    pulp = load_pulp_pairs(args.pulp_pkgs)
    covered = load_advisory_pairs(args.advisories_dir, args.advisories_poc_dir)
    missing = sorted(pulp - covered)

    if args.json_out:
        print(
            json.dumps(
                {
                    "missing": [f"{n}@{v}" for n, v in missing],
                    "total_pulp": len(pulp),
                    "total_covered": len(covered),
                    "total_missing": len(missing),
                },
                indent=2,
            )
        )
    else:
        print(f"Pulp pairs:    {len(pulp)}")
        print(f"Covered:       {len(covered)}")
        print(f"Missing:       {len(missing)}")
        if missing:
            print()
            for norm_name, version in missing:
                print(f"{norm_name}@{version}")


if __name__ == "__main__":
    main()
