#!/usr/bin/env python3
"""Build Docker image for Hyperswitch."""

import subprocess
from swesmith.profiles.rust import Hyperswitch9474c853
from swesmith.constants import LOG_DIR_ENV, ENV_NAME
from pathlib import Path

profile = Hyperswitch9474c853()

print("=" * 80)
print(f"Building Docker image for {profile.repo}")
print(f"Commit: {profile.commit}")
print(f"Image name: {profile.image_name}")
print("=" * 80)

# Create env directory
env_dir = LOG_DIR_ENV / profile.repo_name
env_dir.mkdir(parents=True, exist_ok=True)

# Write Dockerfile
dockerfile_content = profile.dockerfile
dockerfile_path = env_dir / "Dockerfile"
with open(dockerfile_path, "w") as f:
    f.write(dockerfile_content)

print(f"\nDockerfile written to: {dockerfile_path}")
print("\nDockerfile content:")
print("-" * 80)
print(dockerfile_content[:2000])
print("-" * 80)

# Build command
build_cmd = (
    f"docker build --platform linux/arm64 "
    f"-t {profile.image_name} "
    f"{env_dir}"
)

print(f"\nBuilding with command:")
print(build_cmd)
print()

# Run build
result = subprocess.run(
    build_cmd,
    shell=True,
    capture_output=False,
)

if result.returncode == 0:
    print("\n✅ Build successful!")
else:
    print(f"\n❌ Build failed with return code: {result.returncode}")
