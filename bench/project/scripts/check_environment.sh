#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

mode="${1:-core}"
case "$mode" in
  core) required=(bash gcc g++ make python3 tar gzip sha256sum) ;;
  --with-mpvas) required=(bash gcc g++ make python3 tar gzip sha256sum pkg-config sed flex bison) ;;
  *) echo "usage: $0 [--with-mpvas]" >&2; exit 2 ;;
esac

missing=()
for command in "${required[@]}"; do
  command -v "$command" >/dev/null 2>&1 || missing+=("$command")
done

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "ERROR: native execution is supported on Linux x86_64; use Docker on other hosts." >&2
  exit 1
fi
if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "ERROR: the bundled Bazel binary targets Linux x86_64." >&2
  exit 1
fi
if ((${#missing[@]})); then
  printf 'ERROR: missing required commands:' >&2
  printf ' %s' "${missing[@]}" >&2
  printf '\nRun scripts/install_system_deps_ubuntu.sh on Ubuntu/Debian.\n' >&2
  exit 1
fi

CHECK_MPVAS="$mode" "${PYTHON_SOURCE:-python3}" - <<'PY'
import pathlib
import os
import sys
import sysconfig

if os.environ["CHECK_MPVAS"] == "--with-mpvas":
    if sys.version_info[:2] != (3, 10):
        raise SystemExit(
            "ERROR: the pinned Charm/mPVAS build requires Python 3.10.x; "
            "use PYTHON_SOURCE=python3.10 or the Ubuntu 22.04 Dockerfile"
        )
elif sys.version_info < (3, 8):
    raise SystemExit("ERROR: Python >= 3.8 is required")
print(f"python={sys.version.split()[0]}")
if os.environ["CHECK_MPVAS"] == "--with-mpvas":
    import venv

    header = pathlib.Path(sysconfig.get_paths()["include"]) / "Python.h"
    if not header.is_file():
        raise SystemExit(f"ERROR: Python development headers are missing: {header}")
    print(f"python_header={header}")
PY

[[ -s "$BAZEL" ]] || { echo "ERROR: bundled Bazel is missing: $BAZEL" >&2; exit 1; }
[[ -d "$DISTDIR" ]] || { echo "ERROR: Bazel distdir is missing: $DISTDIR" >&2; exit 1; }
if [[ "$mode" == "--with-mpvas" ]]; then
  [[ -s "$ROOT/third_party/pbc-0.5.14.tar.gz" ]] || { echo "ERROR: PBC source is missing" >&2; exit 1; }
  [[ -s "$ROOT/third_party/charm-0.50-2b58a7e.tar.gz" ]] || { echo "ERROR: Charm source is missing" >&2; exit 1; }
  [[ -s "$ROOT/third_party/python-wheels/setuptools-83.0.0-py3-none-any.whl" ]] || { echo "ERROR: setuptools wheel is missing" >&2; exit 1; }
  pkg-config --exists gmp || { echo "ERROR: GMP development files are missing (install libgmp-dev)" >&2; exit 1; }
  pkg-config --exists openssl || { echo "ERROR: OpenSSL development files are missing (install libssl-dev)" >&2; exit 1; }
fi

echo "host=$(uname -srmo)"
echo "gcc=$(gcc -dumpfullversion -dumpversion)"
echo "bazel=$BAZEL"
echo "environment_profile=$mode"
echo "environment_check=pass"
