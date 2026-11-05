#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "$0")" && pwd)/env.sh"
BIN="$PACKAGE_ROOT/project/third_party/bazel-8.1.1-linux-x86_64"
chmod +x "$BIN"
# Ignore inherited .bazelrc paths to old hosts; no TLS verification is disabled.
exec "$BIN" --ignore_all_rc_files "--output_user_root=$BAZEL_USER_ROOT" "$@"
