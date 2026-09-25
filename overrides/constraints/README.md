# Constraint files

Pins that let a package build when resolving its dependencies from scratch
would not produce a working set.

## Why these are sometimes needed

fromager resolves each requirement to the newest version that satisfies it and
**never backtracks**. It also resolves against PyPI *as it is today*, not as it
was when the package was released. So building a version that is not the newest
can walk into a dependency graph that has no solution — some transitive
dependency has since raised a floor that an older direct dependency caps — and
the build fails at the end of bootstrap with a conflict rather than a compile
error.

A constraints file hands fromager the answer a backtracking resolver would have
found. Generate one with `hack/generate-constraints.py` rather than by hand.

## Naming

Files are looked up by `hack/identify-constraints`, most specific first:

```
overrides/constraints/<package>-<version>.txt
overrides/constraints/<package>.txt
```

`<package>` is the **`onboarded_packages/` JSON filename stem, verbatim** — so
`Flask-WTF`, not `flask-wtf`. (This differs from `overrides/settings/`, which
follows fromager's own underscore convention: `nvidia_nat_atif.yaml` for the
package onboarded as `nvidia-nat-atif.json`.)

`<version>` is the `version` field from that JSON. For a package with an
`sdist_url`, that is the version, **not** the git tag: `deltalake-1.6.6.txt`,
even though the tag is `python-v1.6.6`.

Prefer the version-scoped form. The pins that make one version resolve routinely
contradict another version of the same package, so an unversioned file becomes a
trap the next time the package is bumped.

## Contents

Plain `pip` constraint syntax — one `name==version` per line, names in PEP 503
form (`pydantic-core`, not `pydantic_core`). fromager normalizes before matching,
so either spelling binds; normalized is what `hack/generate-constraints.py`
emits, and a file that mixes both is just harder to read. Note this is the
opposite of the *filename* rule above, which takes the JSON stem verbatim.

Keep it to the packages that actually need pinning; `hack/generate-constraints.py`
defaults to the dependencies whose resolved version is not the newest on PyPI,
which is the set that will drift.

## What a file is allowed to change

`hack/generate-constraints.py` resolves the package twice, with and without the
file, and records the result in the header. Read that header in review: it is
what the pins actually do, which is not evident from the pin list.

Most files here will say *"Without this file the package does not resolve at
all"* — that is the case constraints exist for, and the header then lists the
versions the pins produce. Where the package does resolve unaided the header is
a diff instead, and a diff of `(none)` means the file is dead weight: fromager
already reaches those versions on its own.

Pins are taken from pip's *install* closure, and an install dependency is never
installed to build a wheel, so the usual pin cannot change how anything
compiles. But constraints bind by name across the whole run, and fromager
resolves build-time edges too, so a pin on a name that is also someone's
build requirement — `packaging`, `setuptools`, `wheel`, `numpy`, `cython` —
changes the environment wheels are compiled in.

Two things have to coincide before that can alter a compiled artifact, and the
header records both:

- the pinned name is reached by a `build-system`, `build-backend` or
  `build-sdist` edge, so it lands in some package's build environment; **and**
- the pinned *version* is not already in the index. A wheel the index serves is
  downloaded, not rebuilt, so the pin had no say in how it was compiled. Being
  onboarded is not the test — the index serves many versions per package, and
  the onboarded one is only the newest to build.

The second condition is the one that bites, because the default pin set is
exactly the dependencies whose version is *not* the newest — the versions least
likely to be in the index already. Whatever a run builds first is the copy the
index serves from then on, so a first build under a pin is permanent.

The script refuses to write a file when both hold, unless you pass
`--allow-build-impact`.

Constraints are applied **by name across the whole build**, not just under the
package that needed them. A pin here therefore also binds any other package
built in the same run, including a top-level build of the pinned package itself
— which is why files are kept per package and per version rather than merged
into one index-wide list.

## How they are applied

`hack/identify-constraints` runs alongside `hack/identify-packages` in the build
pipeline, matches the packages being built against this directory, and passes
the matches to the `build-wheels` task's `CONSTRAINT_FILES` param, which turns
each into a `-c` flag. Packages with no file here contribute nothing.

**At most one file per build.** `build-wheels` documents `-c` as repeatable, but
fromager declares `--constraints-file` as a plain string, so a second `-c`
overwrites the first and the earlier files are dropped without a warning — the
build then goes green having applied only the last one. `identify-constraints`
fails the build rather than let that happen, so if one commit builds two
packages that both have files here, split it into two commits. A `build_extra`
co-build cannot be split — the extra is always built with its parent — so there
the pins have to be reconciled into one file, and if they contradict, nothing at
this layer can help.

**One file still reaches every package in the build.** Passing the guard is not
the same as being safe. `build-wheels` hands the selected file to *each*
package's fromager run, and constraints bind by name across a whole resolution,
so a package with no file of its own is still resolved under someone else's
pins. If those pins contradict it the build fails loudly, which is fine; if they
merely pull its dependencies backwards it builds green and nothing says so.
`identify-constraints` warns whenever a file is selected and more than one
package is being built. Read that warning — it is the only signal, because
`hack/generate-constraints.py` resolves just the package its file is named for
and cannot see a co-build coming.

To reproduce locally:

```bash
hack/build-locally.sh -c overrides/constraints/<package>-<version>.txt '<package>==<version>'
```
