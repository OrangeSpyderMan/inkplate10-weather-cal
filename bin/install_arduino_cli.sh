#!/usr/bin/env bash
set -euo pipefail

VERSION="${ARDUINO_CLI_VERSION:-1.3.1}"
INSTALL_DIR="${ARDUINO_CLI_INSTALL_DIR:-.tools}"

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
  x86_64|amd64) arch_name="64bit" ;;
  aarch64|arm64) arch_name="ARM64" ;;
  armv7l|armv6l) arch_name="ARMv7" ;;
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

curl -fsSL "$url" -o "$tmpdir/$archive"
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