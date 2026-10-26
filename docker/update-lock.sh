#!/usr/bin/env bash
# Regenerate docker/requirements.lock -- every dependency of the image, pinned by
# version and SHA-256.
#
# It runs INSIDE the image's own pinned base, so the lock is resolved by the same Python
# (3.14) and the same platform (Linux, glibc) that installs it. pip-compile records
# every wheel hash of each pinned release, so the one file serves amd64 and arm64.
# The resolution has no platform markers today; if one appears, check that it does not
# differ between the two architectures.
#
#   ./docker/update-lock.sh            then rebuild and run the test stage
#
# Needs podman or docker. Takes several minutes (pip-tools hashes every wheel).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

ENGINE="${ENGINE:-$(command -v podman || command -v docker)}"
BASE="$(sed -n 's/^FROM \(.*python[^ ]*\) AS base$/\1/p' Dockerfile)"
[ -n "$BASE" ] || { echo "could not find the base image in the Dockerfile"; exit 1; }

"$ENGINE" run --rm -v "$PWD:/src:Z" -w /src "$BASE" sh -c '
    pip install -q pip-tools &&
    pip-compile -q --generate-hashes --allow-unsafe --strip-extras --extra semantic \
        --no-emit-index-url --output-file=docker/requirements.lock \
        docker/build-requirements.in pyproject.toml'
echo "docker/requirements.lock updated -- review the diff"
