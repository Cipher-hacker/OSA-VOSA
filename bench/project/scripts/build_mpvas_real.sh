#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
PYTHON_SOURCE="${PYTHON_SOURCE:-python3}"
PYTHON_SOURCE="$PYTHON_SOURCE" bash "$ROOT/scripts/check_environment.sh" --with-mpvas

MPVAS_RUNTIME="$RUNTIME_DIR/mpvas-real"
PBC_PREFIX="$MPVAS_RUNTIME/pbc"
PBC_SOURCE="$MPVAS_RUNTIME/src/pbc-0.5.14"
CHARM_SOURCE="$MPVAS_RUNTIME/src/charm"
VENV="$MPVAS_RUNTIME/venv"
SETUPTOOLS_WHEEL="$ROOT/third_party/python-wheels/setuptools-83.0.0-py3-none-any.whl"
JOBS="${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)}"

verify_mpvas_runtime() {
  LD_LIBRARY_PATH="$PBC_PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  PYTHONPATH="$CHARM_SOURCE:$ROOT/source/mpvas-experiments/src" \
    "$VENV/bin/python" - <<'PY'
from charm.core.math.pairing import G1, G2, pair
from charm.toolbox.pairinggroup import PairingGroup

group = PairingGroup("MNT224")
assert pair(group.random(G1), group.random(G2)) is not None
print("mpvas_real_pairing=pass")
PY

  local self_test_log="$MPVAS_RUNTIME/protocol-self-test.log"
  if ! LD_LIBRARY_PATH="$PBC_PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    PYTHONPATH="$CHARM_SOURCE:$ROOT/source/mpvas-experiments/src" \
      "$VENV/bin/python" -m testrun.bench_ud \
        -p ud -n 3 -m 0 --dropout-ratio 0 --suppress-writes \
        >"$self_test_log" 2>&1; then
    echo "ERROR: Charm compiled, but the mPVAS protocol equation failed." >&2
    echo "Use Python 3.10.x or the package Dockerfile." >&2
    cat "$self_test_log" >&2
    return 1
  fi
  echo "mpvas_protocol_self_test=pass"
}

archive_hashes="$({
  sha256sum "$ROOT/third_party/pbc-0.5.14.tar.gz"
  sha256sum "$ROOT/third_party/charm-0.50-2b58a7e.tar.gz"
  sha256sum "$SETUPTOOLS_WHEEL"
  sha256sum "$ROOT/source/mpvas-experiments/src/mpvas/plain/protocol.py"
  sha256sum "$ROOT/source/mpvas-experiments/src/mpvas/ud/protocol.py"
  sha256sum "$ROOT/source/mpvas-experiments/src/testrun/bench_ud.py"
  "$PYTHON_SOURCE" --version
} | sha256sum | cut -d' ' -f1)"
if [[ -f "$MPVAS_RUNTIME/.built" ]] && [[ "$(cat "$MPVAS_RUNTIME/.built")" == "$archive_hashes" ]]; then
  verify_mpvas_runtime
  exit 0
fi

rm -rf "$MPVAS_RUNTIME"
mkdir -p "$MPVAS_RUNTIME/src" "$PBC_PREFIX"
tar -xzf "$ROOT/third_party/pbc-0.5.14.tar.gz" -C "$MPVAS_RUNTIME/src"
pushd "$PBC_SOURCE" >/dev/null
./configure --prefix="$PBC_PREFIX"
make -j"$JOBS"
make install
popd >/dev/null

"$PYTHON_SOURCE" -m venv --without-pip "$VENV"
SETUPTOOLS_WHEEL="$SETUPTOOLS_WHEEL" "$VENV/bin/python" - <<'PY'
import os
import pathlib
import sysconfig
import zipfile

wheel = pathlib.Path(os.environ["SETUPTOOLS_WHEEL"])
site_packages = pathlib.Path(sysconfig.get_paths()["purelib"])
with zipfile.ZipFile(wheel) as archive:
    archive.extractall(site_packages)

import setuptools

print(f"setuptools={setuptools.__version__}")
PY
tar -xzf "$ROOT/third_party/charm-0.50-2b58a7e.tar.gz" -C "$MPVAS_RUNTIME/src"
extracted_charm="$(find "$MPVAS_RUNTIME/src" -maxdepth 1 -type d -name 'charm-*' | head -n 1)"
[[ -n "$extracted_charm" ]] || { echo "ERROR: Charm archive did not extract" >&2; exit 1; }
mv "$extracted_charm" "$CHARM_SOURCE"

# Charm's removed `setup.py test` hook is unrelated to the pairing extension.
# Patch only the disposable build copy; the pinned upstream archive stays exact.
sed -i '/from setuptools\.command\.test import test as TestCommand/d' "$CHARM_SOURCE/setup.py"
sed -i '/^class PyTest(TestCommand):$/,/^$/d' "$CHARM_SOURCE/setup.py"
sed -i "s/cmdclass={'uninstall':UninstallCommand,'test':PyTest}/cmdclass={'uninstall':UninstallCommand}/" "$CHARM_SOURCE/setup.py"
if grep -Eq 'TestCommand|class PyTest|[^a-zA-Z]PyTest' "$CHARM_SOURCE/setup.py"; then
  echo "ERROR: obsolete Charm test hook patch was incomplete" >&2
  exit 1
fi

pushd "$CHARM_SOURCE" >/dev/null
CPP='gcc -E' ./configure.sh \
  --python="$VENV/bin/python" \
  --disable-ecc \
  --disable-werror \
  "--extra-cflags=-I$PBC_PREFIX/include -I$PBC_PREFIX/include/pbc" \
  "--extra-ldflags=-L$PBC_PREFIX/lib -Wl,-rpath,$PBC_PREFIX/lib"
LD_LIBRARY_PATH="$PBC_PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  "$VENV/bin/python" setup.py build_ext --inplace
popd >/dev/null

verify_mpvas_runtime

printf '%s\n' "$archive_hashes" > "$MPVAS_RUNTIME/.built"
echo "mpvas_build=pass"
