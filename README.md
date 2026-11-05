# OSA Paper Artifact: Oblivious Secure Aggregation

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Rust: 1.87+](https://img.shields.io/badge/Rust-1.87%2B-orange.svg)](https://www.rust-lang.org/)

This repository provides the complete artifact package for **OSA** (Oblivious Secure Aggregation) and **VOSA** (Verifiable Oblivious Secure Aggregation), including:
- Formal performance benchmarks comparing **OSA**, **OPA**, **Willow**, and **TACITA-STE**.
- Verification benchmarks comparing **VOSA** and **mPVAS-UD**.
- Full experimental parameter grid evaluations across participating client counts ($n$), committee sizes ($m$), vector lengths ($L$), and client dropout rates ($\delta$).
- End-to-end CPU federated learning training modules (MNIST with LeNet-5, CIFAR-10 with AlexNet-BN).
- Complete historical raw measurement evidence and automated figure reproduction scripts for publication-ready figures.

Use `all` to execute the complete end-to-end experiment pipeline on a Linux host (354 protocol measurements and four CPU federated learning training runs of 300 rounds each). Use `paper` to run the 354 benchmark measurements alone, or `replay` to recreate the 66 publication figures directly from bundled historical evidence.

---

## 1. Replaying Figures from Bundled Evidence

To verify reproduction locally on Windows or Linux without running long server benchmarks, run the following commands from the repository root:

```bash
python -m pip install -r bench/requirements.txt
python artifact.py verify
python artifact.py test
python artifact.py replay
```

- **`verify`**: Verifies native source integrity, training datasets, threshold configurations, message communication bounds, 3-repeat consistency, and all 1,058 historical plot points.
- **`test`**: Runs the complete unit and regression test suite covering protocol contracts, alignment logic, and figure plotting constraints.
- **`replay`**: Reads archived reference measurements and generates an independent `outputs/replay_<timestamp>/` directory containing all 66 figures in PNG and PDF formats, along with point-by-point raw CSV datasets and `index.json`.

---

## 2. Benchmarking on a Linux Host / AutoDL Server

### System Requirements
- **OS**: Linux x86_64 (e.g., Ubuntu 20.04 / 22.04 LTS).
- **CPU**: Physical multicore CPU with at least 32 available logical threads (benchmarks default to 32 worker threads).
- **RAM**: At least 32 GiB system memory recommended.
- **Disk Space**: At least 20 GiB available disk space (place in a dedicated data disk if using AutoDL).
- **Environment**: Python **3.10.x** (with `python3.10-dev` headers), Rust toolchain, and C/C++ compilation tools (for Charm/PBC pairing in mPVAS).

### Full Pipeline Execution
Execute from the repository root:

```bash
cd /root/autodl-tmp/OSA-Paper-Artifact
python3 --version
python3 artifact.py plan &&
python3 artifact.py setup --install-system &&
python3 artifact.py build &&
python3 artifact.py smoke &&
python3 artifact.py setup-training &&
python3 -u artifact.py all
```

> **Note**: `python3` refers to Python 3.10.x. If the system default differs, use `python3.10`. On Debian/Ubuntu, `--install-system` installs required APT compilation dependencies. Build automation utilizes the bundled Bazel 8.1.1, local caches, and TACITA's locked Cargo vendor dependencies.

### Command Breakdown
- **`build`**: Compiles native binaries and builds the Charm/PBC pairing environment.
- **`smoke`**: Executes a small-scale smoke run across all six protocols to validate correctness.
- **`all`**: Sequentially executes the four-protocol comparison (216 runs), OSA grid measurements (72 runs), VOSA/mPVAS verification benchmarks (90 runs), figure plotting, and four 300-round federated training experiments.
- **`paper`**: Executes all 354 benchmark measurements and renders figures (omitting training setup).
- **`WORKFLOW`**: Note the `WORKFLOW` directory printed upon launch. If interrupted, resume with:
  ```bash
  python3 -u artifact.py all --resume /absolute/path/to/WORKFLOW
  ```

---

## 3. Modular Stage Execution & Resuming

Individual stages can be executed or resumed independently:

```bash
# 1. Four-protocol comparison (OSA, OPA, Willow, TACITA)
python3 -u artifact.py comparison

# 2. Supplementary OSA parameter grid
python3 -u artifact.py grid --comparison /absolute/path/to/combined_RUN

# 3. Verification benchmark (VOSA vs. mPVAS)
python3 -u artifact.py verification

# 4. Export publication figures
python3 artifact.py figures \
  --comparison /absolute/path/to/combined_RUN \
  --verification /absolute/path/to/verification_RUN \
  --grid /absolute/path/to/grid_RUN
```

- If an individual stage is interrupted, pass `--resume /absolute/path/to/raw_RESULTS` to the corresponding command.
- The `grid` stage automatically verifies host specifications, baseline source fingerprints, and the compiled OSA binary hash.

---

## 4. Experimental Datasets & Metric Definitions

### Benchmark Overview

| Experiment Scope | Measurements | Target Figures | Description |
| :--- | :---: | :---: | :--- |
| **Four Protocols** (OSA, OPA, Willow, TACITA) | 216 | 45 | Fixed baseline, participant count ($n$), committee size ($m$), vector length ($L$), and dropout rate ($\delta$) |
| **OSA Parameter Grid** | 72 (24 existing + 48 new) | 16 | $n \in \{100, 200, 400, 600, 800, 1000\}$ under $\delta \in \{0\%, 10\%, 20\%, 30\%\}$ (8 grid plots + 8 fixed-$n$ slices) |
| **VOSA vs. mPVAS Verification** | 90 | 18 | Verification overhead curves across varying client population and dropout |
| **Cumulative Workload Reference** | (Shared) | 3 | Cumulative role computational overhead reference curves |

### Default Parameter Configuration
- **Selected Clients $n$**: Default $n = 1000$. Under default 20% dropout, surviving active clients $A = 800$.
- **Model Vector Length $L$**: Default $L = 10000$. Swept over $[10000, 100000]$.
- **Committee Size $m$ & Threshold $t$**: Committee sizes $m \in \{40, 50, 60, 80, 100\}$ map to response thresholds $t \in \{32, 40, 48, 65, 85\}$ (default $m=50, t=40$).
- **OSA Ramp Secret Sharing**: $(40, 30, 50)$ RSS over Mersenne prime field $\mathbb{F}_{2^{31}-1}$ (benchmark ramp dimension $d=16$).
- **RLWE KAHE Scheme**: Google `shell-encryption` with degree $N_1=8192$, plaintext modulus $t_1=2^{168}$, 4-component RNS ciphertext modulus $q_1$, $\sigma_s=4.5$, $\sigma_e=6.36396$.
- **Repetitions**: Every configuration is executed across 3 independent trials; reported values represent sample averages.

### Metric Definitions
1. **`Running time (s)`**:
   - Measures individual role computation time for cryptographic transformations, serialization, and aggregation:
     - `active_client_computation`: Computation time of a single client.
     - `committee_responder_computation`: Computation time of a single committee member.
     - `server_computation`: Computation time of the central server.
     - `verifier_computation`: Computation time of the independent verifier.
     - `server_plus_verifier_computation`: Aggregate computation time of the server and verifier.
2. **`Per-round completion time (s)`**:
   - Measures the total wall-clock elapsed duration of a complete aggregation round from client dispatch to final model reconstruction, capturing concurrent worker execution and pipeline synchronization.
3. **`Communication (KB)` / `Communication (MB)` / `Total communication (MB)`**:
   - Accounts for directed payload bytes transmitted per round by each role. Excludes one-time offline setup parameters and broadcast global model downloads.

---

## 5. Standalone Federated Learning (FedAvg)

To evaluate real model convergence under CPU execution, 300-round federated training can be executed independently:

```bash
python3 artifact.py setup-training
python3 -u artifact.py training
```

- **Tasks**:
  1. CIFAR-10 with AlexNet-BN (OSA vs. Plain baseline, 300 rounds).
  2. MNIST with LeNet-5 (OSA vs. Plain baseline, 300 rounds).
- **Concurrency**: 4 OMP/MKL threads, 1 OpenBLAS thread, and 8 concurrent OSA aggregation chunks. RSS dimension is configured to 30.
- **Resuming**: Resume interrupted training runs with `training --resume /absolute/path/to/training_RUN`.

---

## 6. Directory Structure

```
OSA-Paper-Artifact/
├── artifact.py                      # Unified CLI entry point (build, test, replay, all, training)
├── README.md                        # Complete technical documentation & execution guide
├── .gitattributes                   # Line ending & binary checksum protection (* -text)
├── .gitignore                       # Build caches and temporary run exclusions
├── .github/workflows/reproduce.yml  # GitHub Actions CI automated replay workflow
├── bench/                           # Core native benchmark implementations & harness
│   ├── config.json                  # Experiment matrix and benchmark configuration
│   ├── package_manifest.json        # Integrity manifest verifying 7,189 source files
│   ├── requirements.txt             # Python runtime dependencies
│   ├── scripts/                     # Native execution and scheduler scripts
│   ├── tests/                       # Protocol contract and test suites
│   ├── tacita/                      # TACITA STE/KAHE baseline implementation & Cargo vendor
│   └── project/
│       ├── source/                  # Native implementations of OSA, VOSA, OPA, Willow, mPVAS
│       ├── third_party/             # Bazel 8.1.1, PBC, Charm-Crypto, and Boringssl dependencies
│       └── federated_training/      # Real FedAvg training modules, data loaders, and models
├── datasets/                        # Pre-packaged MNIST and CIFAR-10 datasets
├── experiments/                     # Orchestration, verification, and publication plotting logic
│   ├── align.py                     # Benchmark runner and threshold alignment driver
│   ├── datasets.py                  # Dataset integrity verification routines
│   ├── figures.json                 # Precise publication figure specifications & coordinates
│   ├── osa_grid.py                  # OSA parameter grid execution and slice plot definitions
│   ├── publication.py               # Publication figure renderer (PNG, PDF, CSV)
│   ├── setup_training.sh            # FL C++ aggregation bridge build script
│   └── tests/                       # Test suites for full plan, alignment, and figure generation
├── reference/                       # Archived formal reference measurements & figure datasets
│   ├── comparison/                  # 216 four-protocol comparison runs and manifest
│   ├── verification/                # 90 VOSA/mPVAS verification runs
│   └── figure-values.csv            # Canonical plot point verification table
└── outputs/
    └── reference-replay/            # Pre-generated replayed figures and datasets
        ├── paper/                   # 66 publication figures (PNG, PDF, CSV)
        ├── reference/               # Reference comparison plots
        ├── comparison-runs.csv      # Unified 4-protocol measurement table
        ├── verification-runs.csv    # Unified verification measurement table
        └── index.json               # Metric-to-file artifact index
```

---

## 7. Automated CI Verification

A GitHub Actions workflow ([`.github/workflows/reproduce.yml`](.github/workflows/reproduce.yml)) is included to validate the artifact automatically:
- Executes `python artifact.py verify` to validate all source file hashes, datasets, and measurement records.
- Executes `python artifact.py test` to run all unit tests.
- Executes `python artifact.py replay` to regenerate all 66 figures and verify reproducibility.
