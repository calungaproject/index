import aiohttp
import asyncio
import typing
import json
import logging

LOGGER = logging.getLogger()
ONBOARDED_PKGS_PATH = "onboarded_packages.json"
PYPI_MAX_CONCURRENT_REQUEST = 10


def main():
    logging.basicConfig(level=logging.DEBUG)
    # onboarded_pkgs maps pkg names to onboarded versions and ignored versions
    onboarded_pkgs: dict[str, dict[str, list[str]]] = get_onboarded_packages()
    pypi_versions: dict[str, set[str]] = asyncio.run(get_pypi_versions(onboarded_pkgs.keys()))
    pulp_versions: dict[str, set[str]] = get_pulp_versions(onboarded_pkgs.keys())
    results: list[str] = compile_result(onboarded_pkgs, pypi_versions, pulp_versions)
    print(results)


def get_onboarded_packages():
    with open(ONBOARDED_PKGS_PATH) as f:
        return json.load(f)


async def get_pypi_versions(packages: typing.Sequence) -> dict[str, set[str]]:
    result = {}
    semaphore = asyncio.Semaphore(PYPI_MAX_CONCURRENT_REQUEST)

    async with aiohttp.ClientSession() as s:
        async def get_pkg(pkg):
            url = f"https://pypi.org/pypi/{pkg}/json"
            async with semaphore:
                try:
                    async with s.get(url, timeout=10) as resp:
                        if not resp.ok:
                            LOGGER.warning(f"Error getting pypi data for {pkg}: {resp.status}")
                            return
                        data = await resp.json()
                        result[pkg] = set(data["releases"].keys())
                except Exception as e:
                    LOGGER.warning(f"Exception raised getting pypi data for {pkg}: {str(e)}")
        tasks = [get_pkg(pkg) for pkg in packages]
        await asyncio.gather(*tasks)
    return result


def get_pulp_versions(packages: typing.Sequence):
    return {
        "urllib3": set(),
        "numpy": set(),
    }


def compile_result(onboarded_pkgs, pypi_versions, pulp_versions):
    results = []
    for pkg in pypi_versions.keys():
        to_build = pypi_versions[pkg] - pulp_versions[pkg] -  set(onboarded_pkgs[pkg]['ignored_versions'])
        results.extend([f"{pkg}=={ver}" for ver in to_build])
    return results


if __name__ == "__main__":
    main()
