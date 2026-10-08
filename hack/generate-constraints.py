#!/usr/bin/env python3
"""Generate a fromager constraints file for a package version.

Asks pip for a resolution -- pip backtracks, fromager does not -- and writes
the subset fromager would not have reached on its own. See
overrides/constraints/README.md for why that is needed and why fromager
never backtracks.

This script resolves from wheel metadata rather than sdists. Wheel metadata
declares the same dependencies without running each candidate's build
backend, so one old sdist that a current backend refuses to process cannot
abort the whole resolve.

By default the subset is the dependencies whose resolved version is *not* the
newest release on PyPI -- those are exactly the ones fromager would overshoot,
and they are also the ones that will drift further as PyPI moves on. Use
--full to pin the entire resolution instead.

The resolution runs inside the builder image so that the Python version and the
platform tags match what CI will use.

Requires:
    podman  -- runs the builder image; always needed.
    yq, tkn -- read the builder image out of the pinned build-wheels task
               bundle, the same two steps hack/build-locally.sh takes. Only
               needed when --builder-image is not given, and reading the
               bundle also needs pull access to the registry it lives in.

Usage:
    hack/generate-constraints.py 'google-adk==1.36.2'
    hack/generate-constraints.py --full --output - 'google-adk==1.36.2'
    hack/generate-constraints.py --builder-image quay.io/...@sha256:... 'pkg==1.0'
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


def capture(cmd: list[str]) -> str:
    """Run a command for its stdout, reporting why it failed if it does.

    subprocess captures stderr, so an unhandled CalledProcessError shows a
    traceback and an exit status with the tool's own message discarded --
    `tkn bundle list` against a registry we cannot pull from says only
    "returned non-zero exit status 1" when the real answer is "unauthorized".
    """
    # Suppressed for semgrep, the SAST scanner CI runs: the rule flags cmd for
    # being a parameter rather than a literal, but run() defaults to
    # shell=False, so every element is one argument and none can become a
    # command.
    try:
        result = subprocess.run(  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit
            cmd, capture_output=True, text=True, check=True
        )
    except subprocess.CalledProcessError as err:
        detail = (err.stderr or "").strip() or "(no output on stderr)"
        raise RuntimeError(f"{shlex.join(cmd)} failed:\n{detail}") from err
    return result.stdout


def builder_image() -> str:
    """Read the builder image out of the pinned build-wheels task bundle.

    Same two steps hack/build-locally.sh takes, so both resolve against the
    image CI will actually build with.
    """
    bundle = capture(
        [
            "yq",
            '.spec.tasks[] | select(.name == "build-wheels").taskRef.params[]'
            ' | select(.name == "bundle") | .value',
            str(PIPELINE),
        ]
    ).strip()

    listing = capture(["tkn", "bundle", "list", "-o", "json", bundle])
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
        # resolution without fetching it. --index-url pins the resolve to PyPI
        # rather than relying on the image's default. python3 -m pip because
        # the image has no pip on PATH. Not --no-binary :all: -- see the
        # module docstring.
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


# fromager's wording when it genuinely cannot resolve. Anything else out of a
# failed --sdist-only run is the verifier falling over, not a verdict on the
# pins: yandexcloud 0.410.0 fails it with "invalid or unparsed metadata"
# because --sdist-only reads metadata from the sdist, while a real build reads
# it from the wheel and succeeds. Treating that as "the pins are wrong" told
# the user the opposite of the truth.
RESOLUTION_FAILURE_MARKERS = (
    "no single version meets all requirements",
    "could not produce a pip compatible constraints file",
    "unable to resolve requirement specifier",
    "could not find a version that satisfies",
    "resolutionimpossible",
    "no matching distribution found",
)


def is_resolution_failure(stderr: str) -> bool:
    """Whether a failed resolve was fromager rejecting the versions on offer.

    A false negative here is the safe direction: an unrecognised error is
    reported as unverifiable rather than as proof the pins are wrong.
    """
    haystack = stderr.lower()
    return any(marker in haystack for marker in RESOLUTION_FAILURE_MARKERS)


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

        # Suppressed for semgrep as in capture(): argv list, no shell. spec
        # sits after "--" so fromager cannot read it as an option.
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
    spec: str, image: str, content: str, pins: list[tuple[str, str]], full: bool
) -> tuple[list[str] | None, list[str] | None, bool]:
    """Report what the constraints file actually changes about the resolution.

    Resolves twice, with and without the file, and diffs the two graphs. A pin
    that only moves an install dependency cannot affect how anything is built.
    A pin that moves something reached by a build edge changes the environment
    wheels are compiled in, and whatever that run builds first becomes the
    copy the index serves from then on -- so those are reported separately.

    Returns the two lists and whether the package resolved without the file.
    The lists are None when the check could not run at all, which is distinct
    from running and finding no changes.
    """
    print(
        "Verifying what the pins change (resolving twice; minutes on a large graph)",
        file=sys.stderr,
    )
    before, before_error = bootstrap_graph(spec, image, None)
    after, after_error = bootstrap_graph(spec, image, content)

    # A failed resolve with the file is fatal only when fromager actually
    # rejected the versions. If it fell over for some other reason the verifier
    # has no verdict to give, and saying "these pins do not work" would be a
    # guess -- one that is wrong whenever --sdist-only cannot read metadata a
    # real build can.
    if after is None and is_resolution_failure(after_error):
        hint = "The file would not fix the build."
        if before is None and not full:
            # Pinning more only helps when the not-newest subset was too small
            # to settle the conflict. --full also pins the install closure,
            # which can contradict a build-system requirement, so it is offered
            # as something to try rather than as the answer.
            hint += (
                " --full pins the whole resolution rather than only the"
                " not-newest packages and may do better, but check the result:"
                " it pins from the install closure, and those pins bind across"
                " build-system edges too."
            )
        sys.exit(f"these pins do not make {spec} resolve:\n{after_error}\n\n{hint}")

    if after is None:
        # Unverifiable, not wrong. Say so plainly and let the file through --
        # the caller marks it unverified so nobody mistakes silence for a pass.
        print(
            f"warning: could not verify these pins -- fromager failed for a "
            f"reason that is not a resolution conflict, so the check has no "
            f"verdict to give. Confirm with a real build:\n"
            f"  hack/build-locally.sh -c <file> '{spec}'\n"
            f"fromager said:\n{after_error}",
            file=sys.stderr,
        )
        return None, None, before is not None

    if before is None and not is_resolution_failure(before_error):
        print(
            f"warning: could not establish a baseline -- resolving {spec} "
            f"without the file failed for a reason that is not a resolution "
            f"conflict, so there is nothing to compare against and no way to "
            f"tell whether these pins change how anything is built. Confirm "
            f"with a real build:\n"
            f"  hack/build-locally.sh -c <file> '{spec}'\n"
            f"fromager said:\n{before_error}",
            file=sys.stderr,
        )
        return None, None, False

    if before is None:
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
        # No build_impact: the refusal has no meaning when the alternative is
        # that the package cannot be built at all.
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
        # Build-impacting only when both hold: the pin lands in a build
        # environment, and it selects a version the index would have to build.
        # The pin still decides which wheel is used either way, but one the
        # index already serves is downloaded rather than compiled, so the pin
        # cannot have shaped its build.
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


def newest_on_pypi(name: str) -> str:
    """Return the version PyPI reports as current. Exits if PyPI is unreachable."""
    try:
        with urllib.request.urlopen(PYPI_JSON_URL.format(name=name), timeout=30) as f:
            return json.load(f)["info"]["version"]
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError) as err:
        sys.exit(
            f"could not read {name} from PyPI: {err}\n\n"
            "Cannot tell which dependencies are not newest, so the pin set "
            "would be wrong. Retry when PyPI is reachable, or pass --full to "
            "pin the whole resolution without consulting PyPI."
        )


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
        # PyPI resolves the normalized name, so no need for the reported one.
        if newest_on_pypi(name) != version:
            pins.append((name, version))
    return sorted(pins)


def render(
    spec: str,
    pins: list[tuple[str, str]],
    total: int,
    full: bool,
    effects: list[str] | None = None,
    resolved_unconstrained: bool = True,
    unverified: bool = False,
) -> str:
    scope = "full resolution" if full else "not-newest dependencies only"
    lines = [
        f"# Constraints for {spec}",
        "#",
        f"# Generated by hack/generate-constraints.py ({scope}).",
        f"# {len(pins)} of {total} resolved packages pinned.",
    ]
    if unverified:
        lines += [
            "#",
            "# NOT VERIFIED. The check could not run -- fromager failed for a",
            "# reason that was not a resolution conflict -- so nothing here says",
            "# these pins work, and the build-impact guard did not run either.",
            "# Confirm with a real build before relying on it:",
            f"#   hack/build-locally.sh -c <this file> '{spec}'",
        ]
    if effects is not None:
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
        except RuntimeError as err:
            # Most often no pull access to the registry holding the bundle.
            sys.exit(f"{err}\n\nPass --builder-image to skip this lookup.")

    print(f"Resolving {args.requirement} in {image}", file=sys.stderr)

    entries = resolve(args.requirement, image)
    pins = select_pins(entries, name, args.full)
    content = render(args.requirement, pins, len(entries), args.full)

    if pins and not args.no_verify:
        benign, build_impact, resolved_unconstrained = verify_pins(
            args.requirement, image, content, pins, args.full
        )
        unverified = benign is None
        benign = benign or []
        build_impact = build_impact or []
        for line in benign + build_impact:
            print(f"  {line}", file=sys.stderr)
        unchanged = not benign and not build_impact
        if not unverified and resolved_unconstrained and unchanged:
            print(
                "warning: these pins change nothing -- fromager already resolves "
                "to these versions on its own, so the file would have no effect. "
                "The package probably does not need constraints.",
                file=sys.stderr,
            )
        if build_impact and not args.allow_build_impact:  # never when unverified
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
            None if unverified else benign + build_impact,
            resolved_unconstrained,
            unverified,
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
