# Red Hat Trusted Libraries (RHTL) Getting Started Guide

## Service Account Setup

Create a service account in the [terms-based registry](https://access.redhat.com/terms-based-registry/accounts)

  `<username>` is your ########|service-account-name

  `<password>` is your token. It's important to keep this private.


## Setting up your environment

To learn more about Red Hat Trusted Libraries compatibility, check out our [Support Matrices.](support_matrices.md)

You need to have python3.12 installed on your system. At this time, all wheels in the trusted-libraries index
are built with [Python3.12.12](https://www.python.org/downloads/release/python-31212/)

You have a few options for setting up your python + pip environment to look at the Trusted Libraries index.

### pip

You can create a `pip.conf` file in your environment and `pip` will automatically find it.

You have a few options on where you place it, which will be detailed below.

Here's an example `pip.conf` :
```
[global]
index-url = https://<username>:<password>@packages.redhat.com/trusted-libraries/python/
```

You can handle this locally by placing your `pip.conf` into your virtual environment or `venv`: `venv/pip.conf`

You can handle this for your user by putting it at `~/.config/pip/pip.conf`

You can place it for any user on your computer at `/etc/xdg/pip/pip.conf` or `/etc/pip.conf`

To read more about this `pip.conf` setup, look [here](https://pip.pypa.io/en/stable/topics/configuration/)

### uv

If you use `uv` you can configure your `pyproject.toml` to look at the RHTL index.

```toml
[[tool.uv.index]]
name = "trusted-libraries"
url = "https://packages.redhat.com/trusted-libraries/python"
# use RHTL index as your default index
default = true
```

You also need to export your credentials so `uv` can authenticate with the index.

```bash
export UV_INDEX_INTERNAL_PROXY_USERNAME=<username>
export UV_INDEX_INTERNAL_PROXY_PASSWORD=<password>
```

To read more about `uv` configuration, look [here](https://docs.astral.sh/uv/concepts/indexes/)

### netrc

Some tools also use `netrc` and you may want to configure the index there.

Sample `~/.netrc`
```
machine packages.redhat.com login <username> password <password>
```

For more information on using `netrc` look [here](https://pip.pypa.io/en/stable/topics/authentication/#netrc-support)

## Using the index

### Installing with pip
Here are the steps needed to install packages from the index.

Take a look at [Python Packaging User Guide](https://packaging.python.org/en/latest/tutorials/installing-packages/) for more information

1. create a virtual environment `venv`.

```bash
python3.12 -m venv venv
source venv/bin/activate
```

If you are choosing the local `pip.conf` strategy: `cp my-pip.conf venv/`

Here are some options for installing from the index:
```bash
pip install numpy
pip install numpy==2.3.5
pip install -r requirements.txt
pip install --no-cache-dir -r requirements.txt
pip install --only-binary=:all: -r requirements.txt

```

To learn more about `pip install` go [here](https://pip.pypa.io/en/stable/cli/pip_install/)

### Installing with uv

To learn more about managing dependencies with `uv` go [here](https://docs.astral.sh/uv/concepts/projects/dependencies/)

