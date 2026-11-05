#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "$0")" && pwd)/env.sh"
LOG="$PACKAGE_ROOT/logs/build_$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$LOG") 2>&1
trap 'code=$?; echo "BUILD_FAILED exit=$code line=$LINENO log=$LOG"; exit "$code"' ERR
[[ -x "$PYTHON" ]] || { echo "Run setup.sh first"; exit 1; }
export BAZEL="$PACKAGE_ROOT/scripts/bazel.sh"
chmod +x "$BAZEL"
FLAGS=(-c opt --force_pic "--jobs=$JOBS" "--distdir=$DISTDIR" "--repository_cache=$BAZEL_REPOSITORY_CACHE"
       "--experimental_downloader_config=$PACKAGE_ROOT/project/rust_mirror.cfg"
       --action_env=CC=gcc --action_env=CXX=g++ --repo_env=CC=gcc --repo_env=CXX=g++)
SECURE="$PACKAGE_ROOT/project/source/secure-aggregation-main"
OPA="$PACKAGE_ROOT/project/source/opa-secure-aggregation"
mkdir -p "$SECURE/tacita_ste_bridge_fig7_v2" "$PACKAGE_ROOT/bin" "$PACKAGE_ROOT/tacita/bin"
cp "$PACKAGE_ROOT/tacita/bridge/BUILD" "$PACKAGE_ROOT/tacita/bridge/kahe_stage.rs" "$SECURE/tacita_ste_bridge_fig7_v2/"
cd "$SECURE"
"$BAZEL" test "${FLAGS[@]}" //willow/protocol:osa_shell_kahe_unit_test \
    //willow/protocol:vosa_verification_test //willow/protocol:willow_full_aux_test
"$BAZEL" build "${FLAGS[@]}" //willow/benches:run_osa_compact_seeded_unified \
    //willow/benches:run_willow_paper_full_unified //willow/benches:run_vosa_verification_only \
    //tacita_ste_bridge_fig7_v2:kahe_stage
cp bazel-bin/willow/benches/run_osa_compact_seeded_unified "$PACKAGE_ROOT/bin/osa"
cp bazel-bin/willow/benches/run_willow_paper_full_unified "$PACKAGE_ROOT/bin/willow"
cp bazel-bin/willow/benches/run_vosa_verification_only "$PACKAGE_ROOT/bin/vosa"
cp bazel-bin/tacita_ste_bridge_fig7_v2/kahe_stage "$PACKAGE_ROOT/tacita/bin/kahe_stage_fig7_v2"
cd "$OPA"
"$BAZEL" build "${FLAGS[@]}" //opa:bench_opalwr_optimized
cp bazel-bin/opa/bench_opalwr_optimized "$PACKAGE_ROOT/bin/opa"
cd "$PACKAGE_ROOT/tacita"
cargo +1.87.0 build --release --locked --offline -j "$JOBS"
BENCH_CLIENT_WARMUP=0 BENCH_CLIENT_SAMPLES=1 cargo +1.87.0 test --release --locked --offline -j "$JOBS"
"$PYTHON" -m unittest discover -s tests -p 'test_fig7_*.py'
# The dedicated validator uses the just-built bridge instead of a different Bazel cache.
BENCH_CLIENT_WARMUP=0 BENCH_CLIENT_SAMPLES=1 "$PYTHON" "$PACKAGE_ROOT/scripts/validate_ste.py"
if [[ "${INCLUDE_VERIFICATION:-0}" == "1" ]]; then
    PYTHON_SOURCE="$PYTHON" bash "$PACKAGE_ROOT/project/scripts/build_mpvas_real.sh"
fi
cd "$PACKAGE_ROOT"
"$PYTHON" -m unittest discover -s tests -v
echo "BUILD_PASS log=$LOG"
