#!/usr/bin/env bash
set -euo pipefail

VERSION="${ARDUINO_CLI_VERSION:-1.3.1}"
INSTALL_DIR="${ARDUINO_CLI_INSTALL_DIR:-.tools}"
EXPECTED_SHA256="${ARDUINO_CLI_SHA256:-}"

usage() {
  cat <<EOF
Usage: ${0##*/} [VERSION]

Downloads Arduino CLI into a repo-local tools directory without committing the
binary. Defaults:
  VERSION=${VERSION}
  INSTALL_DIR=${INSTALL_DIR}

Environment overrides:
  ARDUINO_CLI_VERSION
  ARDUINO_CLI_INSTALL_DIR
  ARDUINO_CLI_SHA256 (required when overriding VERSION)
EOF
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

if [ "${1:-}" != "" ]; then
  VERSION="$1"
fi

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
DEST_DIR="$REPO_ROOT/$INSTALL_DIR"

os=$(uname -s | tr '[:upper:]' '[:lower:]')
arch=$(uname -m)

case "$os" in
  linux) os_name="Linux" ;;
  darwin) os_name="macOS" ;;
  *)
    echo "Unsupported OS: $os" >&2
    exit 1
    ;;
esac

case "$arch" in
  x86_64|amd64)
    arch_name="64bit"
    if [ "$os_name" = "Linux" ]; then
      archive_sha256="376428d7d45be640c00812a71612e1742edc2f5f9ee3742a2d6da7870e079588"
    else
      archive_sha256="a0ca73f7e599d33a532fb2b1f8af9260541d990a684988bf8208abb42be83ced"
    fi
    ;;
  aarch64|arm64)
    arch_name="ARM64"
    if [ "$os_name" = "Linux" ]; then
      archive_sha256="cf4f668b1add7a20310d79e83a1f3eb148031f95e5673d9171c65f5e9a126a94"
    else
      archive_sha256="723f8ddaf4875e87a20380c88c15ff9d55916c3c80cb432f1f980ea32cec2341"
    fi
    ;;
  armv7l)
    [ "$os_name" = "Linux" ] || {
      echo "Unsupported OS/architecture combination: $os/$arch" >&2
      exit 1
    }
    arch_name="ARMv7"
    archive_sha256="5bede31df9e4c3456c851207079fea0852e9190e71841196e489b1b31184e05d"
    ;;
  armv6l)
    [ "$os_name" = "Linux" ] || {
      echo "Unsupported OS/architecture combination: $os/$arch" >&2
      exit 1
    }
    arch_name="ARMv6"
    archive_sha256="689060b93711d5bebf32bf5f64ce6e6718d3c5715985b351c8736a8f39d6bfa5"
    ;;
  *)
    echo "Unsupported architecture: $arch" >&2
    exit 1
    ;;
esac

archive="arduino-cli_${VERSION}_${os_name}_${arch_name}.tar.gz"
url="https://github.com/arduino/arduino-cli/releases/download/v${VERSION}/${archive}"

mkdir -p "$DEST_DIR"

tmpdir=$(mktemp -d)
cleanup() {
  rm -rf "$tmpdir"
}
trap cleanup EXIT

echo "Downloading Arduino CLI ${VERSION} from:"
echo "  $url"

if [ "$VERSION" != "1.3.1" ] && [ -z "$EXPECTED_SHA256" ]; then
  echo "ARDUINO_CLI_SHA256 is required when overriding VERSION." >&2
  exit 1
fi
if [ -n "$EXPECTED_SHA256" ]; then
  archive_sha256="$EXPECTED_SHA256"
fi
curl -fsSL "$url" -o "$tmpdir/$archive"
if command -v sha256sum >/dev/null 2>&1; then
  actual_sha256=$(sha256sum "$tmpdir/$archive" | awk '{print $1}')
else
  actual_sha256=$(shasum -a 256 "$tmpdir/$archive" | awk '{print $1}')
fi
if [ "$actual_sha256" != "$archive_sha256" ]; then
  echo "Checksum verification failed for $archive" >&2
  exit 1
fi
echo "Checksum verified: $archive"
tar -xzf "$tmpdir/$archive" -C "$tmpdir"
install -m 0755 "$tmpdir/arduino-cli" "$DEST_DIR/arduino-cli"

echo "Installed:"
"$DEST_DIR/arduino-cli" version
echo
echo "Use with:"
echo "  make firmware-setup"
echo "  make firmware-compile"
echo
echo
echo "Checking host dependencies..."
if ! command -v python3 >/dev/null 2>&1; then
  echo "WARNING: 'python3' was not found in your PATH. It is required for firmware upload."
else
  echo "Found: $(python3 --version 2>&1)"
  if ! python3 -c "import serial" >/dev/null 2>&1; then
    echo "WARNING: 'pyserial' is not installed for python3. It is required for firmware upload."
  else
    echo "Found: pyserial"
  fi
fi
