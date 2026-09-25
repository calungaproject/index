#!/usr/bin/env python3
"""Generate a fromager constraints file for a package version.

fromager resolves each requirement to the newest matching version and never
backtracks, so building a version that is not the newest can land in a
dependency graph with no solution. pip does backtrack. This script asks pip for
a resolution, then writes the subset of it that fromager would not have reached
on its own.

By default that subset is the dependencies whose resolved version is *not* the
newest release on PyPI -- those are exactly the ones fromager would overshoot,
and they are also the ones that will drift further as PyPI moves on. Use
--full to pin the entire resolution instead.

The resolution runs inside the builder image so that the Python version, the
platform tags, and the index configuration match what CI will use.

Usage:
    hack/generate-constraints.py 'google-adk==1.36.2'
    hack/generate-constraints.py --full --output - 'google-adk==1.36.2'
"""

import argparse
import json
import re
import shlex
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PIPELINE = REPO_ROOT / ".tekton" / "build-pipeline.yaml"
CONSTRAINTS_DIR = REPO_ROOT / "overrides" / "constraints"
PYPI_JSON_URL = "https://pypi.org/pypi/{name}/json"
PYPI_SIMPLE_URL = "https://pypi.org/simple"
CACHE_SERVER_URL = (
    "https://packages.redhat.com/api/pypi/public-trusted-libraries/main/simple/"
)

# Edge types that put a package into another package's build environment. An
# install dependency is never installed to build a wheel, so pinning one cannot
# change how anything compiles; pinning one of these can.
BUILD_EDGE_TYPES = frozenset({"build-system", "build-backend", "build-sdist"})

# What a pinned version means for the build: a wheel the index already serves is
# downloaded rather than rebuilt, so the pin cannot have shaped it.
IN_INDEX = "already in the index"
WILL_BUILD = "will be built"
DROPPED = "no longer in the build"
INDEX_UNKNOWN = "index unreachable, assuming it will be built"


def normalize(name: str) -> str:
    """Normalize a distribution name to its PEP 503 form."""
    return re.sub(r"[-_.]+", "-", name).lower()


def split_requirement(spec: str) -> tuple[str, str]:
    """Split a '<name>==<version>' requirement into its two parts."""
    name, sep, version = spec.partition("==")
    if not sep or not name.strip() or not version.strip():
        raise ValueError(f"expected '<name>==<version>', got {spec!r}")
    return name.strip(), version.strip()


def builder_image() -> str:
    """Read the builder image out of the pinned build-wheels task bundle.

    Same two steps hack/build-locally.sh takes, so both resolve against the
    image CI will actually build with.
    """
    bundle = subprocess.run(
        [
            "yq",
            '.spec.tasks[] | select(.name == "build-wheels").taskRef.params[]'
            ' | select(.name == "bundle") | .value',
            str(PIPELINE),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    listing = subprocess.run(
        ["tkn", "bundle", "list", "-o", "json", bundle],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    for step in json.loads(listing)["spec"]["steps"]:
        if step["name"] == "build-wheels":
            return step["image"]
    raise RuntimeError(f"no build-wheels step in {bundle}")


def onboarded_stem(name: str) -> str | None:
    """Return the onboarded_packages/ filename stem matching a package name.

    identify-constraints keys on that stem verbatim (it is what
    identify-packages emits), and it is not always the normalized name --
    Flask-WTF.json is onboarded under its mixed-case name.
    """
    target = normalize(name)
    for path in (REPO_ROOT / "onboarded_packages").glob("*.json"):
        if normalize(path.stem) == target:
            return path.stem
    return None


def resolve(spec: str, image: str) -> list[dict]:
    """Resolve a requirement with pip inside the builder image.

    Returns the ``install`` entries of pip's ``--report``. pip backtracks where
    fromager does not, so this is the resolution fromager needs to be handed.
    """
    script = (
        "set -euo pipefail; "
        # install --dry-run --report, not download: only install reports the
        # resolution without fetching it. --index-url because the image's
        # default index is the internal wheel server, not PyPI. python3 -m pip
        # because the image has no pip on PATH.
        #
        # Deliberately NOT --no-binary :all:. fromager builds from sdists, so
        # resolving from sdists looks like the faithful choice, but it makes pip
        # run each candidate's build backend just to read its metadata, and an
        # old sdist that a current backend refuses to process then aborts the
        # whole resolution -- google-adk 1.36.2 dies on a backtracked-to
        # cloudpickle whose [tool.flit.metadata] table flit_core 4 rejects.
        # That is a packaging failure, not a dependency conflict, and it tells
        # us nothing about the graph. Wheel metadata declares the same
        # dependencies and is what we want here.
        "python3 -m pip install --quiet --ignore-installed "
        f"--index-url {PYPI_SIMPLE_URL} "
        f"--dry-run --report /tmp/report.json -- {shlex.quote(spec)} >/dev/null; "
        "cat /tmp/report.json"
    )
    result = subprocess.run(
        ["podman", "run", "--rm", image, "bash", "-c", script],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.exit(
            f"pip could not resolve {spec}:\n{result.stderr.strip()}\n\n"
            "If pip cannot resolve it either, no constraints file will help; "
            "the version genuinely has no solution against today's PyPI."
        )
    return json.loads(result.stdout)["install"]


def bootstrap_graph(
    spec: str, image: str, constraints: str | None
) -> tuple[dict[str, tuple[str, frozenset[str]]] | None, str]:
    """Resolve a requirement with fromager and return its dependency graph.

    Maps each distribution to the version fromager picked and the set of edge
    types it was reached by. Uses --sdist-only, which resolves without building
    wheels, and the cache server, so this costs a resolution rather than a
    build -- still minutes on a large graph.

    Returns ``(None, stderr_tail)`` when fromager cannot resolve. Failure is not
    an error here: a package that does not resolve unconstrained is the exact
    case a constraints file exists for, so the caller decides what it means.
    """
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp) / "work"
        workdir.mkdir()
        mounts = ["-v", f"{workdir}:/var/workdir:Z"]
        prefix = []
        if constraints is not None:
            constraints_path = Path(tmp) / "constraints.txt"
            constraints_path.write_text(constraints)
            mounts += ["-v", f"{constraints_path}:/tmp/constraints.txt:ro,Z"]
            prefix = ["--constraints-file", "/tmp/constraints.txt"]

        # No shell is involved: podman is exec'd from an argv list, so nothing
        # here is parsed by a shell and shlex would corrupt the arguments rather
        # than protect them. spec sits after "--" so it cannot be read as a
        # fromager option either, and both it and image come from this script's
        # own argv or the digest pinned in the build pipeline, never from a
        # remote source. The one call in this file that does use a shell,
        # resolve(), quotes its interpolation.
        result = subprocess.run(  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit
            ["podman", "run", "--rm", *mounts, "-w", "/var/workdir", image, "fromager"]
            + ["--settings-file", "/usr/local/share/fromager/overrides/settings.yaml"]
            + prefix
            # -c here is the cache server, not a constraints file: fromager
            # spells --constraints-file -c globally and --cache-wheel-server-url
            # -c on the subcommand.
            + ["bootstrap", "--sdist-only", "-c", CACHE_SERVER_URL, "--", spec],
            capture_output=True,
            text=True,
        )
        graph_file = workdir / "work-dir" / "graph.json"
        if result.returncode != 0 or not graph_file.is_file():
            return None, result.stderr.strip()[-2000:]

        graph: dict[str, tuple[str, frozenset[str]]] = {}
        kinds: dict[str, set[str]] = {}
        versions: dict[str, str] = {}
        for node in json.loads(graph_file.read_text()).values():
            for edge in node.get("edges", []):
                name, sep, version = edge["key"].rpartition("==")
                if not sep:
                    continue
                name = normalize(name)
                versions[name] = version
                kinds.setdefault(name, set()).add(edge.get("req_type", "unknown"))
        for name, version in versions.items():
            graph[name] = (version, frozenset(kinds[name]))
        return graph, ""


def verify_pins(
    spec: str, image: str, content: str, pins: list[tuple[str, str]]
) -> tuple[list[str], list[str], bool]:
    """Report what the constraints file actually changes about the resolution.

    Resolves twice, with and without the file, and diffs the two graphs. A pin
    that only moves an install dependency cannot affect how anything is built.
    A pin that moves something reached by a build edge changes the environment
    wheels are compiled in, and whatever that run builds first becomes the
    copy the index serves from then on -- so those are reported separately.

    Returns the two lists and whether the package resolved without the file.
    """
    print(
        "Verifying what the pins change (resolving twice; minutes on a large graph)",
        file=sys.stderr,
    )
    before, before_error = bootstrap_graph(spec, image, None)
    after, after_error = bootstrap_graph(spec, image, content)

    # The file failing to resolve is the one outcome that is always fatal: these
    # pins are supposed to be the answer, and they are not.
    if after is None:
        sys.exit(
            f"these pins do not make {spec} resolve:\n{after_error}\n\n"
            "The file would not fix the build. Try --full, which pins the whole "
            "resolution rather than only the not-newest packages."
        )

    if before is None:
        # No baseline to diff against, because there is no unconstrained build:
        # this is the case the whole mechanism exists for. Every pin is
        # load-bearing by definition, so report the resolution the file produces
        # and skip the build-impact refusal -- "changes how a wheel compiles" has
        # no meaning when the alternative is that the wheel cannot be built.
        conflicts = [
            line for line in before_error.splitlines() if " ERROR " in line
        ]
        print(
            f"{spec} does not resolve without this file, which is the case "
            "constraints exist for. Reporting what the pins produce rather than a "
            "diff. fromager said:",
            file=sys.stderr,
        )
        for line in conflicts or before_error.splitlines()[-1:]:
            print(f"  {line}", file=sys.stderr)
        pinned = {name for name, _ in pins}
        effects = [
            f"{name}: unresolvable -> {version} "
            f"[{build_disposition(name, version)}] "
            f"(reached via {', '.join(sorted(edges))})"
            for name, (version, edges) in sorted(after.items())
            if name in pinned
        ]
        return effects, [], False

    benign, build_impact = [], []
    for name in sorted(set(before) | set(after)):
        old_version, old_edges = before.get(name, (None, frozenset()))
        new_version, new_edges = after.get(name, (None, frozenset()))
        if old_version == new_version:
            continue
        # A pin can drop a package from the graph or add one, not just move its
        # version. Take the edge types from both graphs: a dropped node has none
        # in the second, and classifying it by that alone would call a
        # disappearing build backend an install-only change.
        edges = old_edges | new_edges
        change = f"{old_version or 'absent'} -> {new_version or 'absent'}"
        # Two things have to coincide for a pin to change a compiled artifact:
        # the package must land in some build environment, and it must actually
        # be built. A version the index already serves is downloaded, not
        # rebuilt, so the pin had no say in how it was compiled -- refusing on
        # the edge type alone would reject pins that cannot do any harm.
        disposition = build_disposition(name, new_version)
        line = (
            f"{name}: {change} [{disposition}] "
            f"(reached via {', '.join(sorted(edges))})"
        )
        if edges & BUILD_EDGE_TYPES and disposition != IN_INDEX:
            build_impact.append(line)
        else:
            benign.append(line)
    return benign, build_impact, True


def version_from_filename(filename: str) -> str | None:
    """Pull the version out of a wheel or sdist filename."""
    if filename.endswith(".whl"):
        # PEP 427 escapes '-' in the name, so the second field is the version.
        parts = filename[: -len(".whl")].split("-")
        return parts[1] if len(parts) >= 2 else None
    for suffix in (".tar.gz", ".zip"):
        if filename.endswith(suffix):
            return filename[: -len(suffix)].rpartition("-")[2] or None
    return None


_index_versions: dict[str, set[str] | None] = {}


def index_versions(name: str) -> set[str] | None:
    """Versions of a distribution the trusted index already serves.

    None when the index cannot be read. An unreachable index proves nothing, so
    the caller has to assume the wheel would be built.
    """
    if name not in _index_versions:
        url = f"{CACHE_SERVER_URL.rstrip('/')}/{name}/"
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as err:
            # 404 is an answer, not a failure: the index has never served this
            # name, so everything pinned for it will be built.
            if err.code != 404:
                print(f"warning: could not read {name} from the index: {err}",
                      file=sys.stderr)
            _index_versions[name] = set() if err.code == 404 else None
        except (urllib.error.URLError, TimeoutError) as err:
            print(f"warning: could not read {name} from the index: {err}",
                  file=sys.stderr)
            _index_versions[name] = None
        else:
            # Simple-index anchor text is the filename.
            found = re.findall(r">([^<>]+\.(?:whl|tar\.gz|zip))<", body)
            _index_versions[name] = {
                version for version in map(version_from_filename, found) if version
            }
    return _index_versions[name]


def build_disposition(name: str, version: str | None) -> str:
    """Say whether this version gets built or comes out of the index."""
    if version is None:
        # Dropped from the graph entirely. Nothing is built for it, but whatever
        # used to pull it in now builds without it, so this is not harmless
        # either -- there is just no wheel to look up that would prove it is.
        return DROPPED
    versions = index_versions(name)
    if versions is None:
        return INDEX_UNKNOWN
    return IN_INDEX if version in versions else WILL_BUILD


def newest_on_pypi(name: str) -> str | None:
    """Return the version PyPI reports as current, or None if unknown."""
    try:
        with urllib.request.urlopen(PYPI_JSON_URL.format(name=name), timeout=30) as f:
            return json.load(f)["info"]["version"]
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError) as err:
        print(f"warning: could not read {name} from PyPI: {err}", file=sys.stderr)
        return None


def select_pins(entries: list[dict], target: str, full: bool) -> list[tuple[str, str]]:
    """Pick which resolved packages to pin.

    The target package is always excluded: constraints bind by name across the
    whole build, so pinning the target would constrain the very thing being
    built.

    Names are emitted in PEP 503 form. pip reports whatever the package
    declares, so an unnormalized run mixes ``pydantic_core`` in with
    ``opentelemetry-api``. Both resolve the same -- fromager normalizes before
    matching a constraint -- but the file is read by people, and
    overrides/constraints/README.md asks for normalized names.
    """
    pins = []
    for entry in entries:
        meta = entry["metadata"]
        name = normalize(meta["name"])
        version = meta["version"]
        if name == normalize(target):
            continue
        if full:
            pins.append((name, version))
            continue
        # Pin when PyPI is unreachable too: an unnecessary pin costs nothing,
        # whereas a missing one is the failure this file exists to prevent.
        # PyPI resolves the normalized name, so no need for the reported one.
        newest = newest_on_pypi(name)
        if newest != version:
            pins.append((name, version))
    return sorted(pins)


def render(
    spec: str,
    pins: list[tuple[str, str]],
    total: int,
    full: bool,
    effects: list[str] | None = None,
    resolved_unconstrained: bool = True,
) -> str:
    scope = "full resolution" if full else "not-newest dependencies only"
    lines = [
        f"# Constraints for {spec}",
        "#",
        f"# Generated by hack/generate-constraints.py ({scope}).",
        f"# {len(pins)} of {total} resolved packages pinned.",
    ]
    if effects is not None:
        # Record the measured effect, not just the pins. A reviewer can see at a
        # glance which versions this file actually moves, which is not obvious
        # from the pin list: most pins match what would have been resolved
        # anyway and change nothing.
        lines += ["#"]
        if resolved_unconstrained:
            lines += ["# Versions this changes, vs resolving without the file:"]
        else:
            lines += [
                "# Without this file the package does not resolve at all, so there",
                "# is nothing to diff against. Versions the pins produce:",
            ]
        if effects:
            lines += [f"#   {line}" for line in effects]
        else:
            lines += ["#   (none)"]
    lines += [
        "#",
        "# See overrides/constraints/README.md before editing by hand.",
        "",
    ]
    lines += [f"{name}=={version}" for name, version in pins]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("requirement", help="package to resolve, as <name>==<version>")
    parser.add_argument(
        "--full",
        action="store_true",
        help="pin every resolved package, not just the not-newest ones",
    )
    parser.add_argument(
        "--output",
        help="where to write; defaults to the path identify-constraints looks up, "
        "'-' for stdout",
    )
    parser.add_argument(
        "--builder-image",
        help="use this image instead of the one pinned in the build pipeline",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="skip resolving twice to measure what the pins change",
    )
    parser.add_argument(
        "--allow-build-impact",
        action="store_true",
        help="write the file even when a pin changes a build-time dependency",
    )
    args = parser.parse_args()

    try:
        name, version = split_requirement(args.requirement)
    except ValueError as err:
        parser.error(str(err))

    if args.builder_image:
        image = args.builder_image
    else:
        try:
            image = builder_image()
        except FileNotFoundError as err:
            sys.exit(
                f"{err.filename} is not installed. Reading the pinned builder image "
                "needs yq and tkn; pass --builder-image to skip that lookup."
            )

    print(f"Resolving {args.requirement} in {image}", file=sys.stderr)

    entries = resolve(args.requirement, image)
    pins = select_pins(entries, name, args.full)
    content = render(args.requirement, pins, len(entries), args.full)

    if pins and not args.no_verify:
        benign, build_impact, resolved_unconstrained = verify_pins(
            args.requirement, image, content, pins
        )
        for line in benign + build_impact:
            print(f"  {line}", file=sys.stderr)
        if resolved_unconstrained and not benign and not build_impact:
            print(
                "warning: these pins change nothing -- fromager already resolves "
                "to these versions on its own, so the file would have no effect. "
                "The package probably does not need constraints.",
                file=sys.stderr,
            )
        if build_impact and not args.allow_build_impact:
            sys.exit(
                "\nThese pins change a dependency that is installed to build "
                "other packages,\nso they change how those wheels are compiled, "
                "not just which versions are\nused. Whatever this run builds "
                "first is what the index serves from then on.\n"
                "Drop those pins if the build does not need them, or pass "
                "--allow-build-impact."
            )
        content = render(
            args.requirement,
            pins,
            len(entries),
            args.full,
            benign + build_impact,
            resolved_unconstrained,
        )

    if not pins:
        print(
            f"warning: nothing to pin -- every dependency of {args.requirement} "
            "already resolves to its newest release, so a constraints file will "
            "not change what fromager does.",
            file=sys.stderr,
        )

    if args.output == "-":
        sys.stdout.write(content)
        return

    if args.output:
        destination = Path(args.output)
    else:
        stem = onboarded_stem(name)
        if stem is None:
            sys.exit(
                f"{name} is not onboarded, so identify-constraints has nothing to "
                "match against. Onboard it first, or pass --output explicitly."
            )
        destination = CONSTRAINTS_DIR / f"{stem}-{version}.txt"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content)
    shown = (
        destination.relative_to(REPO_ROOT)
        if destination.is_relative_to(REPO_ROOT)
        else destination
    )
    print(
        f"Wrote {len(pins)} pins of {len(entries)} resolved packages to {shown}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
