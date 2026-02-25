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
PULP_USERNAME = os.getenv("SERVICE_ACCOUNT_USERNAME")
PULP_PASSWORD = os.getenv("SERVICE_ACCOUNT_PASSWORD")
PULP_REPOSITORY_VERSION = os.getenv("PULP_REPOSITORY_VERSION")

def fetch_all_packages():
    """Fetch all packages from Pulp using the content list API with pagination."""
    packages = defaultdict(set)

    auth = (PULP_USERNAME, PULP_PASSWORD) if PULP_USERNAME else None
    params = {"limit": 100}

    if PULP_REPOSITORY_VERSION:
        params["repository_version"] = PULP_REPOSITORY_VERSION
        LOGGER.info(f"Filtering by repository_version: {PULP_REPOSITORY_VERSION}")

    url = PULP_CONTENT_API
    LOGGER.info(f"Querying: {url}")

    while url:
        try:
            resp = requests.get(url, params=params, auth=auth, timeout=60)
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

    if not PULP_USERNAME or not PULP_PASSWORD:
        LOGGER.warning("SERVICE_ACCOUNT_USERNAME/PASSWORD not set")

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
