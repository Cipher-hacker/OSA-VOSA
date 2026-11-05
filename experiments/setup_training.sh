#!/usr/bin/env bash
set -euo pipefail
ARTIFACT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ARTIFACT/bench/scripts/env.sh"
PROJECT="$PACKAGE_ROOT/project"
cd "$PROJECT/source/secure-aggregation-main"
bash "$PACKAGE_ROOT/scripts/bazel.sh" build -c opt --force_pic "--jobs=$JOBS" \
    "--distdir=$DISTDIR" "--repository_cache=$BAZEL_REPOSITORY_CACHE" \
    "--experimental_downloader_config=$PROJECT/rust_mirror.cfg" \
    --action_env=CC=gcc --action_env=CXX=g++ --repo_env=CC=gcc --repo_env=CXX=g++ \
    //willow/benches:osa_vosa_shell_kahe_real_fl_bridge
test -x bazel-bin/willow/benches/osa_vosa_shell_kahe_real_fl_bridge
VENV="$RUNTIME_DIR/fl-python"
"$PYTHON" -m venv "$VENV"
"$VENV/bin/python" -m pip install -r "$PACKAGE_ROOT/requirements.txt"
"$VENV/bin/python" -m pip install --no-deps --only-binary=:all: \
    --index-url "${PYTORCH_INDEX_URL:-https://download.pytorch.org/whl/cpu}" \
    torch==2.5.1+cpu torchvision==0.20.1+cpu
# Resolve the CPU wheels' ordinary Python dependencies on the configured PyPI index.
"$VENV/bin/python" -m pip install torch==2.5.1+cpu torchvision==0.20.1+cpu
"$VENV/bin/python" -m pip check
cd "$PROJECT"
"$VENV/bin/python" -m unittest discover -s federated_training/tests -v
"$VENV/bin/python" - "$ARTIFACT/datasets" <<'PY'
import sys
import torch
from pathlib import Path
from federated_training.data import load_datasets
for name, expected in (("mnist", 60000), ("cifar10", 50000)):
    train, test = load_datasets(Path(sys.argv[1]), name, download=False)
    assert (len(train), len(test)) == (expected, 10000)
    print(name, len(train), len(test), "torch", torch.__version__)
PY
echo TRAINING_SETUP_PASS

