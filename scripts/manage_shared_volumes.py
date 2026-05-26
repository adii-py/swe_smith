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

    # Remove old volume, create fresh (fixes OOM from huge target cache)
    python scripts/manage_shared_volumes.py recreate juspay/hyperswitch

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


def remove_volume(repo_name: str, force: bool = False):
    """Remove a shared Docker volume."""
    client = docker.from_env()
    volume_name = get_volume_name(repo_name)

    try:
        volume = client.volumes.get(volume_name)
        if force:
            _stop_containers_using_volume(volume_name)
        volume.remove(force=force)
        print(f"Removed shared volume: {volume_name}")
    except docker.errors.NotFound:
        print(f"Volume does not exist: {volume_name}")
    except docker.errors.APIError as e:
        print(f"Failed to remove {volume_name}: {e}")
        print("Run: python scripts/manage_shared_volumes.py recreate juspay/hyperswitch")


def _stop_containers_using_volume(volume_name: str) -> None:
    """Stop and remove containers that mount the given volume."""
    result = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"volume={volume_name}"],
        capture_output=True,
        text=True,
    )
    ids = [x for x in result.stdout.split() if x.strip()]
    if not ids:
        return
    print(f"Stopping {len(ids)} container(s) using {volume_name}...")
    subprocess.run(["docker", "rm", "-f", *ids], check=False)


def recreate_volume(repo_name: str) -> None:
    """Remove old target volume (if any) and create a fresh empty one."""
    client = docker.from_env()
    volume_name = get_volume_name(repo_name)
    _stop_containers_using_volume(volume_name)
    swesmith_ids = [
        x
        for x in subprocess.run(
            ["docker", "ps", "-aq", "--filter", "name=swesmith"],
            capture_output=True,
            text=True,
        ).stdout.split()
        if x.strip()
    ]
    if swesmith_ids:
        print(f"Removing {len(swesmith_ids)} swesmith container(s)...")
        subprocess.run(["docker", "rm", "-f", *swesmith_ids], check=False)

    try:
        client.volumes.get(volume_name)
        print(f"Removing existing volume: {volume_name}")
        client.volumes.get(volume_name).remove(force=True)
    except docker.errors.NotFound:
        print(f"No existing volume: {volume_name}")

    client.volumes.create(volume_name)
    print(f"Created fresh volume: {volume_name}")
    show_volume_size(volume_name)


def show_volume_size(volume_name: str) -> None:
    result = subprocess.run(
        ["docker", "volume", "inspect", volume_name, "--format", "{{.Mountpoint}}"],
        capture_output=True,
        text=True,
    )
    mount = result.stdout.strip()
    if mount:
        du = subprocess.run(["du", "-sh", mount], capture_output=True, text=True)
        if du.returncode == 0:
            print(f"Volume size on disk: {du.stdout.split()[0]}")


def cleanup_target_dirs():
    """
    Clean up existing target directories in various locations to reclaim disk space.
    The shared Docker volume will handle target directory going forward.
    """
    print("Analyzing disk usage of target directories...")
    print("=" * 60)

    # Locations to check for target directories
    root = Path(__file__).resolve().parents[1]
    locations = [
        Path("/Users/aditya.singh.001/Desktop/hyperswitch/target"),
        root / "juspay__hyperswitch.fece9bc3/target",
        root / "juspay__hyperswitch.c6a70eee/target",
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

    # Recreate command
    recreate_parser = subparsers.add_parser(
        "recreate",
        help="Remove old volume and create a fresh empty target volume",
    )
    recreate_parser.add_argument(
        "repo",
        help="Repository name (e.g., juspay/hyperswitch)",
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
        remove_volume(args.repo, force=True)
    elif args.command == "recreate":
        recreate_volume(args.repo)
    elif args.command == "cleanup":
        cleanup_target_dirs()
    elif args.command == "status":
        show_status()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
