# Red Hat Trusted Libraries (RHTL) Getting Started Guide

> **Note:** Authentication is no longer required to use the RHTL index.
> Earlier versions of this guide covered service account setup, credentials in
> pip/uv config, and `netrc` — those sections were removed because the index is
> now publicly accessible. If you still have credentials configured, you can
> drop them; they are not needed.

## Setting up your environment

To learn more about Red Hat Trusted Libraries compatibility, check out our [Support Matrices](support_matrices.md).

You need to have python3.12 installed on your system. At this time, all wheels in the trusted-libraries index
are built with [Python3.12.12](https://www.python.org/downloads/release/python-31212/).

You have a few options for setting up your python + pip environment to look at the Trusted Libraries index.

### pip

You can create a `pip.conf` file in your environment and `pip` will automatically find it.

You have a few options on where you place it, which will be detailed below.

Here's an example `pip.conf` :
```
[global]
index-url = https://packages.redhat.com/trusted-libraries/python/
```

You can handle this locally by placing your `pip.conf` into your virtual environment or `venv`: `venv/pip.conf`

You can handle this for your user by putting it at `~/.config/pip/pip.conf`

You can place it for any user on your computer at `/etc/xdg/pip/pip.conf` or `/etc/pip.conf`

To read more about this `pip.conf` setup, look [here](https://pip.pypa.io/en/stable/topics/configuration/).

### uv

If you use `uv` you can configure your `pyproject.toml` to look at the RHTL index.

```toml
[[tool.uv.index]]
name = "trusted-libraries"
url = "https://packages.redhat.com/trusted-libraries/python"
# use RHTL index as your default index
default = true
```

To read more about `uv` configuration, look [here](https://docs.astral.sh/uv/concepts/indexes/).

## Using the index

### Installing with pip
Here are the steps needed to install packages from the index.

Take a look at [Python Packaging User Guide](https://packaging.python.org/en/latest/tutorials/installing-packages/) for more information.

1. create a virtual environment `venv`.

```bash
python3.12 -m venv venv
source venv/bin/activate
```

If you are choosing the local `pip.conf` strategy: `cp pip.conf venv/`

Here are some options for installing from the index:
```bash
pip install numpy
pip install numpy==2.3.5
pip install -r requirements.txt
pip install --no-cache-dir -r requirements.txt
pip install --only-binary=:all: -r requirements.txt
```

To learn more about `pip install` go [here](https://pip.pypa.io/en/stable/cli/pip_install/).

### Installing with uv

To learn more about managing dependencies with `uv`, go [here](https://docs.astral.sh/uv/concepts/projects/dependencies/).

### Provenance Attestation Verification

To learn more about verifying RHTL provenance attestations, check out [this repo on GitHub](https://github.com/redhat-tssc-tmm/trusted-libraries/tree/main/blog/scripts).

### Viewing SBOMs

For python wheels, SBOMs are contained inside the wheel at `<package>-X.Y.Z.dist-info/sboms`.
Below is a sample script for viewing an SBOM that came from the RHTL index.

```bash
pip download numpy==2.3.3
unzip numpy-2.3.3-0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl -d extracted
cat extracted/numpy-2.3.3.dist-info/sboms/redhat.spdx.json
```
