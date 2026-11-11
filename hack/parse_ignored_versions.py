#!/usr/bin/env python3
"""
Parse JSON files from onboarded_json/ directory and create a mapping
of package_name==version for all ignored versions.
"""

import json
import sys
from pathlib import Path


def process_package(package_name, input_dir="onboarded_packages", output_dir="."):
    """
    Process a single package and output its ignored versions to a file.

    Args:
        package_name: Name of the package to process
        input_dir: Directory containing the input JSON files
        output_dir: Directory to write the output file

    Returns:
        Number of versions processed, or None if error
    """
    # Construct input file path
    input_file = Path(input_dir) / f"{package_name}.json"

    if not input_file.exists():
        print(f"Error: File '{input_file}' does not exist")
        return None

    try:
        # Read the JSON file
        with open(input_file, 'r') as f:
            data = json.load(f)

        # Get ignored_versions list
        ignored_versions = data.get("ignored_versions", [])

        # Create package_name==version entries
        package_versions = [f"{package_name}=={version}" for version in ignored_versions]
        # reversed_package_versions = [package for package in package_version[::-1]]

        # Write output to txt file
        output_file = Path(output_dir) / f"{package_name}-to-build.txt"
        with open(output_file, 'w') as f:
            [f.write(f"{package_version}\n") for package_version in package_versions[::-1]]

        print(f"Processed {package_name}: {len(ignored_versions)} ignored versions")
        print(f"Output written to: {output_file}")

        return len(ignored_versions)

    except json.JSONDecodeError as e:
        print(f"Error parsing {input_file}: {e}")
        return None
    except Exception as e:
        print(f"Error processing {input_file}: {e}")
        return None


def main():
    """Main function."""
    if len(sys.argv) < 2:
        print("Usage: python parse_ignored_versions.py <package_name>")
        print("Example: python parse_ignored_versions.py boto3")
        sys.exit(1)

    package_name = sys.argv[1]
    process_package(package_name)


if __name__ == "__main__":
    main()
