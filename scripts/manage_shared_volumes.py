#!/usr/bin/env python3
"""
Docker Shared Volume Manager for SWE-smith Rust Projects

Manages shared Docker volumes for Rust target directories to reduce disk usage
across multiple bug generation/validation runs.

Usage:
    # Create shared volume for Hyperswitch
    python scripts/manage_shared_volumes.py create juspay/hyperswitch

    # Clean up old target directories and reclaim disk space
    python scripts/manage_shared_volumes.py cleanup

    # Remove shared volume (destroys compiled artifacts)
    python scripts/manage_shared_volumes.py remove juspay/hyperswitch

    # Show status of all shared volumes
    python scripts/manage_shared_volumes.py status
"""

import argparse
import docker
import subprocess
from pathlib import Path


def get_volume_name(repo_name: str) -> str:
    """Generate volume name from repo name."""
    repo_slug = repo_name.replace("/", "-")
    return f"swesmith-target-{repo_slug}"


def create_volume(repo_name: str):
    """Create a shared Docker volume for a repository."""
    client = docker.from_env()
    volume_name = get_volume_name(repo_name)

    try:
        client.volumes.get(volume_name)
        print(f"Volume already exists: {volume_name}")
    except docker.errors.NotFound:
        client.volumes.create(volume_name)
        print(f"Created shared volume: {volume_name}")

    # Show volume info
    result = subprocess.run(
        ["docker", "volume", "inspect", volume_name],
        capture_output=True,
        text=True
    )
    print(result.stdout)


def remove_volume(repo_name: str):
    """Remove a shared Docker volume."""
    client = docker.from_env()
    volume_name = get_volume_name(repo_name)

    try:
        volume = client.volumes.get(volume_name)
        volume.remove()
        print(f"Removed shared volume: {volume_name}")
    except docker.errors.NotFound:
        print(f"Volume does not exist: {volume_name}")


def cleanup_target_dirs():
    """
    Clean up existing target directories in various locations to reclaim disk space.
    The shared Docker volume will handle target directory going forward.
    """
    print("Analyzing disk usage of target directories...")
    print("=" * 60)

    # Locations to check for target directories
    locations = [
        Path("/Users/aditya.singh.001/Desktop/hyperswitch/target"),
        Path("/Users/aditya.singh.001/Desktop/SWE-smith/juspay__hyperswitch.c6a70eee/target"),
    ]

    total_reclaimed = 0

    for loc in locations:
        if loc.exists():
            # Get size
            result = subprocess.run(
                ["du", "-sh", str(loc)],
                capture_output=True,
                text=True
            )
            size = result.stdout.split()[0] if result.stdout else "unknown"
            print(f"Found: {loc} ({size})")

            response = input(f"  Delete {loc}? [y/N]: ")
            if response.lower() == 'y':
                subprocess.run(["rm", "-rf", str(loc)])
                print(f"  Deleted: {loc}")
                # Estimate size for reporting
                if 'G' in size:
                    try:
                        total_reclaimed += float(size.replace('G', ''))
                    except ValueError:
                        pass
        else:
            print(f"Not found: {loc}")

    print("=" * 60)
    print(f"Estimated space reclaimed: ~{total_reclaimed:.1f} GB")
    print("\nFrom now on, Rust artifacts will be stored in the Docker shared volume:")
    print("  - Volume name: swesmith-target-juspay-hyperswitch")
    print("  - Location: Managed by Docker")
    print("  - Shared across all bug generation/validation containers")


def show_status():
    """Show status of all SWE-smith shared volumes."""
    client = docker.from_env()

    print("SWE-smith Shared Volumes:")
    print("=" * 60)

    volumes = client.volumes.list()
    swesmith_volumes = [v for v in volumes if v.name.startswith("swesmith-target-")]

    if not swesmith_volumes:
        print("No shared volumes found.")
        return

    for vol in swesmith_volumes:
        print(f"\nVolume: {vol.name}")
        # Get detailed info
        result = subprocess.run(
            ["docker", "volume", "inspect", vol.name],
            capture_output=True,
            text=True
        )
        print(result.stdout)

        # Try to get disk usage
        mountpoint = vol.attrs.get('Mountpoint', '')
        if mountpoint:
            result = subprocess.run(
                ["du", "-sh", mountpoint],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                print(f"Size: {result.stdout.split()[0]}")


def main():
    parser = argparse.ArgumentParser(
        description="Manage Docker shared volumes for SWE-smith"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Create command
    create_parser = subparsers.add_parser(
        "create",
        help="Create a shared volume for a repository"
    )
    create_parser.add_argument(
        "repo",
        help="Repository name (e.g., juspay/hyperswitch)"
    )

    # Remove command
    remove_parser = subparsers.add_parser(
        "remove",
        help="Remove a shared volume"
    )
    remove_parser.add_argument(
        "repo",
        help="Repository name (e.g., juspay/hyperswitch)"
    )

    # Cleanup command
    subparsers.add_parser(
        "cleanup",
        help="Clean up old target directories to reclaim disk space"
    )

    # Status command
    subparsers.add_parser(
        "status",
        help="Show status of all shared volumes"
    )

    args = parser.parse_args()

    if args.command == "create":
        create_volume(args.repo)
    elif args.command == "remove":
        remove_volume(args.repo)
    elif args.command == "cleanup":
        cleanup_target_dirs()
    elif args.command == "status":
        show_status()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
