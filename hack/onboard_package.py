import json
import sys
import os
import requests
from packaging.version import Version

ONBOARDED_PKGS_DIR_PATH = "onboarded_packages"
PACKAGES_TXT_PATH = "packages.txt"


def ensure_dir():
    if not os.path.exists(ONBOARDED_PKGS_DIR_PATH):
        os.makedirs(ONBOARDED_PKGS_DIR_PATH)


def get_pkg_path(package_name):
    return os.path.join(ONBOARDED_PKGS_DIR_PATH, f"{package_name}.json")


def load_onboarded_package(package_name):
    path = get_pkg_path(package_name)
    with open(path) as f:
        return json.load(f)


def save_onboarded_package(package_name, package_data):
    path = get_pkg_path(package_name)
    with open(path, "w") as f:
        return json.dump(package_data, f, indent=4)


def get_pypi_versions(package_name):
    url = f"https://pypi.org/pypi/{package_name}/json"
    resp = requests.get(url, timeout=10)
    if not resp.ok:
        print(f"Unexpected status code {resp.status_code} for package {package_name}")
        sys.exit(1)
    return resp.json()["releases"]


def append_new_pkg_version_to_packages_txt(package, version):
    line = f"{package}=={version}"
    with open(PACKAGES_TXT_PATH, "a") as f:
        f.write(f"{line}\n")


def main():
    if len(sys.argv) < 2:
        print("Usage: onboard_package.py <package_name>")
        sys.exit(1)

    ensure_dir()
    pkg_name = sys.argv[1]
    pkg_file = get_pkg_path(pkg_name)

    if os.path.exists(pkg_file):
        print("Package is already onboarded")
        sys.exit(1)

    pypi_versions = get_pypi_versions(pkg_name)
    pkg_versions = list(pypi_versions.keys())
    pkg_versions.sort(key=Version)
    latest = None
    for ver in pkg_versions[::-1]:
        try:
            # multiple elements in list = multiple files
            # yanked value is always consistent among all of them
            yanked = pypi_versions[ver][0]["yanked"]
        except (KeyError, IndexError):
            yanked = True
        if not yanked:
            latest = ver
            break
    if latest is None:
        print("Couldn't find any non-yanked version of the package")
        sys.exit(1)

    # ignore everything but the latest version
    ignored = [v for v in pkg_versions if v != latest]
    package_data = {"ignored_versions": ignored}
    save_onboarded_package(pkg_name, package_data)
    append_new_pkg_version_to_packages_txt(pkg_name, latest)


if __name__ == "__main__":
    main()
