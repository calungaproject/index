#!/usr/bin/env python3
import aiohttp
import asyncio
import json
import logging
import os

LOGGER = logging.getLogger()

ONBOARDED_PKGS_DIR_PATH = "onboarded_packages"

PYPI_MAX_CONCURRENT_REQUEST = 10
PULP_MAX_CONCURRENT_REQUEST = 10

PYPI_URL = "https://pypi.org/pypi/{pkg}/json"
PULP_URL = "https://packages.redhat.com/api/pypi/public-trusted-libraries/main/pypi/{pkg}/json"

PULP_USERNAME = os.getenv("SERVICE_ACCOUNT_USERNAME")
PULP_PWD = os.getenv("SERVICE_ACCOUNT_PASSWORD")


def main():
    logging.basicConfig(level=logging.DEBUG)
    # map of pkg names to onboarded versions and ignored versions
    onboarded_pkgs = get_onboarded_packages()
    # map of pkg names to versions from pypi and pulp
    pkg_releases = asyncio.run(gather_releases(onboarded_pkgs.keys()))
    results = compile_result(onboarded_pkgs, pkg_releases)
    # print here for bash stdout redirection
    print("updates=" + json.dumps(results))


def get_onboarded_packages():
    """Load the files with onboarded packages."""
    onboarded_data = {}
    if not os.path.exists(ONBOARDED_PKGS_DIR_PATH):
        return onboarded_data

    for filename in os.listdir(ONBOARDED_PKGS_DIR_PATH):
        if filename.endswith(".json"):
            pkg_name = filename[:-5]  # strip .json
            path = os.path.join(ONBOARDED_PKGS_DIR_PATH, filename)
            with open(path) as f:
                onboarded_data[pkg_name] = json.load(f)
    return onboarded_data


async def _fetch_releases(session, url, pkg, semaphore, auth=None):
    """
    Fetch releases for specific package from specified url.

    Args:
        session: aiohttp.ClientSession to use
        url: URL for the request
        pkg: package name that's being processed
        semaphore: asyncio.Semaphore for controlling concurrency
        auth: Tuple for basic auth or None

    Returns:
        Set of releases or None in case of any error.

    """
    async with semaphore:
        try:
            async with session.get(url, timeout=10, auth=auth) as response:
                if not response.ok:
                    LOGGER.error(f"Error: {pkg} returned {response.status} from {url}")
                    return None
                data = await response.json()
                return set(data.get("releases", {}).keys())
        except Exception as e:
            LOGGER.error(f"Request failed for {pkg} at {url}: {e}")
            return None


async def gather_releases(packages):
    """Make requests to Pypi and Pulp to get releases for all packages."""
    results = {}

    sem_pypi = asyncio.Semaphore(PYPI_MAX_CONCURRENT_REQUEST)
    sem_pulp = asyncio.Semaphore(PULP_MAX_CONCURRENT_REQUEST)

    async with aiohttp.ClientSession() as session:
        # mapping pkg -> tuple of async tasks
        task_map = {}

        for pkg in packages:
            task_map[pkg] = (
                asyncio.create_task(
                    _fetch_releases(session, PYPI_URL.format(pkg=pkg), pkg, sem_pypi)
                ),
                asyncio.create_task(
                    _fetch_releases(
                        session,
                        PULP_URL.format(pkg=pkg),
                        pkg,
                        sem_pulp,
                        auth=aiohttp.BasicAuth(PULP_USERNAME, PULP_PWD),
                    )
                ),
            )

        for pkg, (pypi_task, pulp_task) in task_map.items():
            pypi_releases = await pypi_task
            pulp_releases = await pulp_task

            results[pkg] = {
                "pypi": pypi_releases,
                "pulp": pulp_releases,
            }

    return results


def compile_result(onboarded_pkgs, pkg_releases):
    """
    Compare what package versions are in pypi, pulp and compile a list of versions that need to be built.
    """
    results = []
    for pkg in onboarded_pkgs.keys():
        # TODO: this should be handled more robustly probably
        # skip those where api calls failed
        if pkg_releases[pkg]["pypi"] is None or pkg_releases[pkg]["pulp"] is None:
            LOGGER.debug(f"Skipping {pkg}")
            continue
        to_build = (
            pkg_releases[pkg]["pypi"]
            - pkg_releases[pkg]["pulp"]
            - set(onboarded_pkgs[pkg]["ignored_versions"])
        )
        results.extend([f"{pkg}=={ver}" for ver in to_build])
    return results


if __name__ == "__main__":
    main()
