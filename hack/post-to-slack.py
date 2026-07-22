#!/usr/bin/env python3
import json
import os
import sys
from datetime import datetime, timezone

import requests


def main():
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        print("SLACK_WEBHOOK_URL environment variable is not set", file=sys.stderr)
        sys.exit(1)

    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <packages_json_file>", file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1]) as f:
        data = json.load(f)

    total_packages = data.get("total_packages")
    total_versions = data.get("total_versions")
    if total_packages is None or total_versions is None:
        sys.exit(1)

    summary = json.dumps(
        {"total_packages": total_packages, "total_versions": total_versions},
        indent=2,
    )
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    text = (
        f"Trusted Libraries Index - Available Packages ({date})\n"
        f"Packages: {total_packages} | Versions: {total_versions}\n"
        f"```{summary}```"
    )

    response = requests.post(webhook_url, json={"text": text}, timeout=30)
    response.raise_for_status()
    print("Slack message sent.")


if __name__ == "__main__":
    main()
