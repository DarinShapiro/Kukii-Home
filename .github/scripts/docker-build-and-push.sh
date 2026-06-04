#!/usr/bin/env bash
# Build + push + verify the add-on image for a release.
#
# Called by semantic-release's @semantic-release/exec `publishCmd` AFTER the
# version files have been bumped in the workspace (via bump-version.cjs in the
# prepareCmd) but BEFORE @semantic-release/git commits them to `main`.
#
# Why this ordering matters: if this script fails, semantic-release aborts the
# whole publish step, so the bump never lands on main. Supervisor (which reads
# config.yaml from main) therefore never sees a version whose image isn't
# already live in GHCR — eliminating the race where the user clicks Update on
# a freshly-published bump commit and hits a 404 because the image hasn't
# finished pushing yet.
#
# Usage: docker-build-and-push.sh <version>
#
# Required env vars (provided by the GitHub Actions job that calls semantic-release):
#   GITHUB_REPOSITORY_OWNER  - the GHCR owner (Mixed-case OK, we lowercase)
#   GITHUB_SHA               - the commit that triggered CI (recorded as
#                              KUKIIHOME_REF in the image)

set -euo pipefail

version="${1:-}"
if [ -z "$version" ]; then
  echo "usage: $0 <version>" >&2
  exit 1
fi
if [ -z "${GITHUB_REPOSITORY_OWNER:-}" ]; then
  echo "GITHUB_REPOSITORY_OWNER not set — must be called from GitHub Actions" >&2
  exit 1
fi

owner=$(echo "${GITHUB_REPOSITORY_OWNER}" | tr '[:upper:]' '[:lower:]')
ref_sha="${GITHUB_SHA:-HEAD}"
image="ghcr.io/${owner}/aarch64-kukiihome-addon"

echo "::group::Build + push ${image}:${version}"
echo "  arch: aarch64 (linux/arm64)"
echo "  sha:  ${ref_sha}"

docker buildx build \
  --platform linux/arm64 \
  --push \
  --tag "${image}:${version}" \
  --tag "${image}:latest" \
  --build-arg "BUILD_FROM=ghcr.io/home-assistant/aarch64-base-debian:bookworm" \
  --build-arg "KUKIIHOME_REF=${ref_sha}" \
  --build-arg "CACHEBUST=${ref_sha}" \
  ha-integration/addon/kukiihome
echo "::endgroup::"

echo "::group::Verify ${image}:${version} is live in GHCR"
if ! docker manifest inspect "${image}:${version}" >/dev/null 2>&1; then
  echo "::error::${image}:${version} not found in GHCR after push — release aborted"
  exit 1
fi
echo "OK — ${image}:${version} resolves in GHCR"
echo "::endgroup::"

echo "Image published and verified. semantic-release may now commit the bump."
