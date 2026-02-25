### General Info

Trusted Libraries content is currently in Technology Preview. To learn more about this, check out [this resource](https://access.redhat.com/solutions/21101)

### Service Account Setup

Create a service account in the [terms-based registry](https://access.redhat.com/terms-based-registry/accounts)

  `<username>` is your ########|service-account-name

  `<password>` is your token. It's important to keep this private.


### Setting up your environment

You need to have python3.12 installed on your system. At this time, all wheels in the trusted-libraries index
are built with [Python3.12.12](https://www.python.org/downloads/release/python-31212/)

You have a few options for setting up your python + pip environment to look at the Trusted Libraries index.

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

Some tools also use `netrc` and you may want to configure the index there.

Sample `~/.netrc`
```
machine packages.redhat.com login <username> password <password>
```

For more information on using `netrc` look [here](https://pip.pypa.io/en/stable/topics/authentication/#netrc-support)

### Using the index

#### Installing
Here are the steps needed to install packages from the index.

Take a look at [Python Packaging User Guide](https://packaging.python.org/en/latest/tutorials/installing-packages/) for more information

1. create a virtual environment `venv`.

```
python3.12 -m venv venv
source venv/bin/activate
```

If you are choosing the local `pip.conf` strategy: `cp my-pip.conf venv/`

Here are some options for installing from the index:
```
pip install numpy
pip install numpy==2.3.5
pip install -r requirements.txt
pip install --no-cache-dir -r requirements.txt

```

To learn more about `pip install` go [here](https://pip.pypa.io/en/stable/cli/pip_install/)

