# OSA paper artifact

This repository connects the four-protocol comparison, the separate VOSA/mPVAS verification experiments and the optional CPU federated training runs. A single entry point supports two kinds of reproduction. `replay` recreates the published figure data from recorded measurements. `paper` takes new measurements on one Linux host.

## 用已有实验记录重新出图

在仓库根目录执行以下命令。核验可在 Windows 或 Linux 上运行，出图需要 Matplotlib。

```bash
python -m pip install -r bench/requirements.txt
python artifact.py verify
python artifact.py test
python artifact.py replay
```

`verify` 检查源文件、训练数据、原生计数、门限、通信量、三次重复和 1,058 个绘图点。成功时打印 `MEASUREMENTS_PASS comparison=216 verification=90 figure_points=1058`。每次 `replay` 创建独立的 `outputs/replay_*/` 目录，包含 PNG、PDF、逐点 CSV、逐次测量和 `index.json`。

## 在 AutoDL 或 Linux 服务器上重测

资源配置为 Linux x86_64、至少 32 个可用逻辑 CPU，以及足够的编译和结果空间。正式程序在启动时要求至少 20 GiB 可用，首次编译还需要额外空间。建议把整个目录放在数据盘。完整验证实验使用 Python **3.10.x** 和对应开发头文件，mPVAS 使用真实的 Charm/PBC pairing。务必从仓库根目录执行。

```bash
cd /root/autodl-tmp/OSA-Paper-Artifact
python3 --version
python3 artifact.py plan
python3 artifact.py setup --install-system
python3 artifact.py build
python3 artifact.py smoke
python3 -u artifact.py paper
```

以上 `python3` 指 Python 3.10.x。若系统默认版本不同，请使用 `python3.10`。Ubuntu/Debian 上 `--install-system` 会安装系统编译依赖，已有依赖可省略此参数。构建自动使用包内 Bazel 8.1.1、项目缓存及 TACITA 锁定的 Cargo vendor 源。首次安装 Rust 1.87、Python 包以及缓存未覆盖的 Bazel 依赖需要网络。运行阶段自动切换至本仓库创建的 Python 环境。Git 检出后可直接运行，网页或网盘上传时也须包含 `.cargo` 等点目录。

`build` 编译并测试四协议原生程序和真实 pairing 环境。`smoke` 以小输入执行六个协议并校验结果。`paper` 在本机新测 216 条四协议记录和 90 条验证记录，随后生成论文图。每组正式配置重复三次。长任务可在 tmux 中执行。请分别保存终端打印的 `COMBINED`、验证实验的 `RESULTS` 以及最终的 `FIGURES` 路径。

### 分开运行及续跑

```bash
python3 -u artifact.py comparison
python3 -u artifact.py verification
python3 artifact.py figures --comparison /absolute/path/to/combined_RUN --verification /absolute/path/to/verification_RUN
```

如果任务中断，用 `comparison --resume /absolute/path/to/paper_aligned_RUN` 或 `verification --resume /absolute/path/to/paper_RUN`。续跑输入是该阶段打印的正式原始 `RESULTS` 目录。四协议绘图输入是合并后打印的 `COMBINED` 目录。续跑检查主机、参数、程序和已完成记录。已完成其中一组时可单独跑另一组，再用 `figures` 合并。若两组均已完成，可反复绘图，无需重复计时。

## 数据来源与读图

| 实验 | 记录 | 对应图 |
| --- | ---: | ---: |
| OSA、OPA、Willow、TACITA-STE 统一回应人数，参与人数、委员会、向量长度和掉线率扫描，含固定配置 | 216 | 45 |
| VOSA 与 mPVAS 验证增量，含 VOSA 的人数与掉线率网格 | 90 | 18 |
| 复用上述验证记录，计算各方累计工作量 | 同一批记录 | 3 张独立参考图 |

默认本轮选中 1,000 位用户，800 份上传成功交付，向量长度 10,000，委员会 50 人，其中 40 人回应。四协议发送端统计逐跳累加，一次性公共参数与全局模型下载在计时通信范围以外。完整上传即使在投递点丢失，也按实验模型计入发送尝试。委员会规模 40、50、60、80、100 分别使用回应门限 32、40、48、65、85。OSA 的该基准使用 RSS 维数 16。客户端额外的 3 次预热、20 次串行计时用于估计单次操作耗时。整轮完成时间直接读取在线阶段计时器。

四协议历史档案含 150 条配置未变的测量及 66 条重测。新服务器的 `comparison` 全量新测 216 条。`Total time (s)` 的四协议曲线使用 `end_to_end_s`。`Server + verifier time (s)` 使用两个角色的时间字段，OSA 的 verifier 分量为零。`driver_wall_s` 另行记录。参考目录中的累计角色工作量单独列示。真实用时随处理器及调度波动，按字节固定的配置可另行逐项比对。

TACITA 为本地 Fig. 7 的 STE + KAHE 实现，省略 MKLHTS。验证组的 mPVAS 使用 mPVAS-UD、K=0、MNT224 和逐坐标向量扩展。VOSA 计验证增量。使用这些结果时请保留实验范围。

## 可选真实联邦学习

在同一台 Linux 服务器执行。此组有独立的 CPU PyTorch 环境和输出目录，按 CIFAR-10 AlexNet-BN OSA、Plain，然后 MNIST LeNet-5 OSA、Plain 的顺序各跑 300 轮。

```bash
python3 artifact.py setup-training
python3 -u artifact.py training
```

先完成通用的 `setup`。训练专用安装编译 OSA 的真实模型更新聚合桥、安装 CPU PyTorch 并运行数据加载和训练单元测试。可用 `PYTORCH_INDEX_URL` 指定可用的 CPU wheel 源。默认沿用独立训练驱动的 4 个 OMP/MKL 线程、1 个 OpenBLAS 线程和 8 个 OSA 并发分块。正式协议性能对比仍使用 32 线程。原训练 JSON 保留自己的 RSS 维数 30。使用 `training --resume /absolute/path/to/training_RUN` 按检查点继续。服务器应为中间模型更新和检查点留出额外磁盘空间。

## 文件组织

| 路径 | 用途 |
| --- | --- |
| `artifact.py` | 公开入口，负责构建、正式实验、续跑、训练与重绘。 |
| `experiments/align.py` | 产出最终统一门限结果的原驱动，核验原始协议输出、运行条件和合并证据。 |
| `experiments/publication.py`, `experiments/figures.json` | 读取原始日志，按实验配置匹配三次实测，将同一套图定义用于历史重放和新测结果。 |
| `experiments/setup_training.sh`, `experiments/datasets.py` | 构建真实 FL 桥、检查训练数据。 |
| `bench/scripts/`, `bench/config.json` | 原始 Linux 环境、Bazel、测试及原生调度代码。原运行树保留自身文件校验；最终发表口径以根目录入口为准。 |
| `bench/project/source/` | OSA/VOSA、Willow、OPA 及 mPVAS 实现。 |
| `bench/tacita/` | TACITA-STE 实现、KAHE 桥、Cargo 锁文件、离线 Rust 依赖和测试。 |
| `bench/project/third_party/` | Bazel、预取依赖、PBC、Charm 和上游许可证。 |
| `bench/project/federated_training/`, `datasets/` | 训练入口、模型、配置、量化与桥调用，及 MNIST、CIFAR-10 数据。 |
| `reference/` | 原始验证日志、统一门限证据、对照绘图值。 |
| `bench/runs/`, `bench/logs/`, `outputs/` | 本机生成的原始测量、日志及最终图表，Git 会忽略这些输出。 |

GitHub Actions 的常规作业进行数据核验、测试及历史重绘。完整原生 smoke 可通过手动 workflow 触发，使用带 `osa-32cpu` 标签的 Linux x64 自托管 runner，需提前备好 Python 3.10 和系统依赖。耗时的正式测量由实验者显式启动。

