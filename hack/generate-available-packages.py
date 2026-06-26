#!/usr/bin/env python3
import json
import logging
import os
import sys
from collections import defaultdict

import requests
from packaging.version import Version

LOGGER = logging.getLogger()

PULP_BASE_URL = os.getenv("PULP_BASE_URL", "https://packages.redhat.com")
PULP_DOMAIN = os.getenv("PULP_DOMAIN", "public-trusted-libraries")
PULP_CONTENT_API = os.getenv(
    "PULP_CONTENT_API",
    f"{PULP_BASE_URL}/api/pulp/{PULP_DOMAIN}/api/v3/content/python/packages/"
)
PULP_REPO_ID = os.getenv("PULP_REPO_ID", "019aa284-e214-77e7-862c-48d5905cccc6")

def fetch_all_packages():
    """Fetch all packages from Pulp using the content list API with pagination."""
    packages = defaultdict(set)

    params = {"limit": 100}

    repo_version_url = f"{PULP_BASE_URL}/api/pulp/{PULP_DOMAIN}/api/v3/repositories/python/python/{PULP_REPO_ID}/"
    try:
        repo_version_response = requests.get(repo_version_url)
    except requests.RequestException as e:
        LOGGER.error(f"Request failed: {e}")
        sys.exit(1)

    response_data = repo_version_response.json()
    PULP_REPOSITORY_VERSION = response_data.get("latest_version_href", None)
    if PULP_REPOSITORY_VERSION is None:
        LOGGER.error(f"Something went wrong with fetching current pulp repository version. {response_data}")
        sys.exit(1)
    LOGGER.info(f"Using pulp repository version: {PULP_REPOSITORY_VERSION}")

    url = PULP_CONTENT_API
    LOGGER.info(f"Querying: {url}")

    while url:
        try:
            resp = requests.get(url, params=params, timeout=60)
        except requests.RequestException as e:
            LOGGER.error(f"Request failed: {e}")
            sys.exit(1)

        if not resp.ok:
            LOGGER.error(f"HTTP {resp.status_code}: {resp.text}")
            sys.exit(1)

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            LOGGER.error(f"Failed to parse JSON: {e}")
            sys.exit(1)

        for pkg in data.get("results", []):
            name = pkg.get("name", "").lower()
            version = pkg.get("version")
            if name and version:
                packages[name].add(version)

        LOGGER.info(f"Fetched {len(data.get('results', []))} items (total packages: {len(packages)})")

        # Next page URL includes params already
        url = data.get("next")
        params = None

    return packages


def main():
    logging.basicConfig(level=logging.DEBUG)

    packages = fetch_all_packages()

    total_versions = sum(len(versions) for versions in packages.values())

    output = {
        "packages": {
            name: {"versions": sorted(versions, key=Version)}
            for name, versions in sorted(packages.items())
        },
        "total_packages": len(packages),
        "total_versions": total_versions
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
