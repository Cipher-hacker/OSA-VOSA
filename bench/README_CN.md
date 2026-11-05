# 新服务器运行说明（round-v2，2026-11-01）

## 这次需要运行的实验

只需四协议 OSA、OPA、Willow、TACITA-STE 的参与人数、委员会大小、向量长度、掉线率扫描。
参数点去重后共216次，每组3次。固定配置与扫描中的相同配置共用真实测量，不另跑。
每次客户端计时都做3次预热、20次串行采样，正式协议仍然只让每个用户上传一次。
因此不必另跑旧的计算补测，不必跑真实联邦学习、VOSA/mPVAS或额外OSA网格。
本包不附旧结果、模型训练数据或本机编译产物。完整原包仍在原来的文件夹。

## 已改动的测量规则

- n表示本轮选中的参与者；默认n=1000，成功聚合A=800，掉线20%。不模拟全局N的抽样。
- 所有n个用户执行和序列化上传。固定前A个上传被投递，其余完整上传在模拟投递点丢失。
  这是明确的整包投递失败模型，不能描述为所有完整消息都已收到却随意丢弃，也不模拟部分上传。
  四协议使用相同前缀掩码；没有网络延迟/带宽/重传模拟。
- 系统总发送量包括全部n次发送尝试、服务器转发、委员回复、Willow验证者发送。
  同一个接收动作不再计费；转发是另一次发送。公共参数、模型广播、协议外传输头不计。
- OSA/OPA向全部m个委员路由份额；Willow验证者及TACITA服务器向m个委员发送请求。
  只有t个委员处理并回复，其余委员发送量为0。单委员图按实际回复者平均，不乘m伪造总量。
- OSA默认(t,d,m)=(40,16,50)。OPA保留打包16、回应阈值34，不把四协议全写成t=40。
- 每轮保留逐用户发送字节/操作时间、逐委员请求和回复字节以及额外采样时间。
- end_to_end_s是连续单调时钟从在线工作开始到结果产出，排除setup和额外计时采样。
  不等于角色耗时相加或平均乘人数。原生三个基线从客户端worker就绪开始计时；
  TACITA等待KAHE和STE均setup/采样完后开始，包含必要的进程间交接和fixture读写。
  TACITA的KAHE用户循环、委员处理仍按原实现串行；STE/基线客户端池上限32线程。
  这不是所有角色同时部署在多台机器上的分布式延迟测试。
- OSA仍是compact seeded-digest RSS31版本，TACITA仍不含MKLHTS。没有改写密码核心算法。

## 参数表

固定值：n=1000，A=800，m=50，L=10000，32线程，3次独立重复。

|扫描|取值|其余固定|
|---|---|---|
|参与人数n|100,200,400,600,800,1000|m=50,L=10000,掉线20%|
|委员会m|40,50,60,80,100|n=1000,L=10000,掉线20%|
|长度L|10000,20000,40000,60000,80000,100000|n=1000,m=50,掉线20%|
|掉线率|10%,20%,30%,40%|n=1000,m=50,L=10000|

委员会扫描阈值：

|m|40|50|60|80|100|
|---|---|---|---|---|---|
|OSA t（d=16）|32|40|48|65|85|
|OPA t（打包16）|27|34|41|54|68|
|Willow/TACITA t|32|40|48|64|80|

不使用旧m=20点：固定d=16时旧t=16不满足d<t，OPA的打包16也不适合原m=20配置。
OSA在较大m时提高t，以满足所选参数的d<t和t>m-d条件。不同协议阈值不同，必须随图说明，
不能把这组扫描宣称为等阈值或已证明等安全强度的对比。

## 上传和执行

上传整个OSA_Updated_Code_20261101文件夹到/root/autodl-tmp/。不要覆盖旧代码。
Ubuntu/Debian x86-64，Python3.10及以上。无需GPU；首次安装需可访问系统/Rust/Python源。
TACITA的Cargo依赖已按原Cargo.lock随包vendor，Cargo构建使用--offline，不会再联网克隆hopcroft-karp。
Bazel和已有distdir随包，Bazel尚未缓存的依赖仍可能需要联网；第一次编译不能承诺全离线。

```bash
cd /root/autodl-tmp/OSA_Updated_Code_20261101
python3 project.py verify
bash run.sh setup --install-system
JOBS=32 bash run.sh build
bash run.sh smoke --scope comparison
python3 project.py plan --scope comparison
bash run.sh paper --scope comparison
```

应依次看到SOURCE_HASH_PASS、SETUP_PASS、BUILD_PASS、smoke的4条PASS和SUITE_COMPLETE。
编译/冒烟任何一步失败都先修复，不能跳过验证直接跑正式实验。
默认正式清单216条；图像只在整套通过核验后输出。

建议正式任务用tmux保活：

```bash
tmux new -s osa_compare
cd /root/autodl-tmp/OSA_Updated_Code_20261101
bash run.sh paper --scope comparison
```

Ctrl+B松开再按D退出会话；重新进入用tmux attach -t osa_compare。

## 结果、日志、继续与打包

运行时打印RESULTS和FIGURES。结果在本包.work/OSA_Paper_Experiments_CPU_20261008/runs下，
该兼容目录名保留旧脚本路径，不代表使用旧数据；contract.version必须是round-v2-20261101。
日志在同层logs；每个任务另有raw/.../attempt_.../process.log、command.json和round_audit.json。
绘图输出paper_figures和plot_data，分别是图片与实际作图数据，PNG/PDF各有一份。
end_to_end_s是新总时间，driver_wall_s包括setup/采样/进程开销，不能用来代替在线总时间。

```bash
# 替换成这一轮输出的真实RESULTS绝对路径：
RUN=/root/autodl-tmp/OSA_Updated_Code_20261101/.work/OSA_Paper_Experiments_CPU_20261008/runs/paper_时间戳
bash run.sh status paper "$RUN"
tail -n 3 "$RUN/measurements.csv"
# 仅同一主机、同一代码/参数/二进制允许断点继续：
bash run.sh paper --scope comparison --resume "$RUN"
# 检验和重绘：
bash run.sh verify-run paper "$RUN"
bash run.sh plot paper "$RUN"
```

如果已经完成整套216次，不要再运行computation。该兼容命令现在只表示同一引擎的48次掉线率子集。
不运行旧的instrument.py、旧prepare.sh或旧补测包。

图形要求：均值连线，不画误差棒，不改实测均值；单位(s)/(KB)/(MB)；系统总量
Total communication (MB)，总时间End-to-end per-round completion time (s)。横轴n用
Number of participating clients n。TACITA压缩其余曲线时，主图三协议，内嵌小图保留四协议；
图例齐全，参数说明放外部文档。没有模型准确率图。

## 代码位置

- project.py、run.sh：校验哈希、准备兼容运行目录、分派运行命令。
- layout.json、manifests：源文件到运行路径映射和SHA256，不能混入另一版本结果。
- config/paper.json：线程、重复、参数扫描、客户端采样次数。
- experiments/shared/paper_suite.py：任务去重、参数阈值、启动/续跑、每发送方向及逐用户校验、结果归一化。
- protocols/secure-aggregation-main/willow/benches/run_osa_full_unified.rs：OSA完整n次上传，A次接收，m次路由/t次回复，d=16，连续整轮计时。
- protocols/opa-secure-aggregation/opa/bench_opalwr_common.rs：OPA同样的测量流程，保留自己的打包/阈值。
- protocols/secure-aggregation-main/willow/benches/run_willow_paper_full_unified.rs：Willow含验证者的完整流程及发送计数。
- protocols/tacita/src/fig7_benchmark.rs：STE setup后READY门闩、n次上传/A次聚合、m次请求/t次回复。
- protocols/tacita/src/main.rs：命令行和延迟fixture载入；使用真实KAHE采样密钥，避免零密钥导致计时偏差。
- protocols/tacita/bridge/kahe_stage.rs：KAHE setup/采样、门闩、n个用户、A个密文求和和最终解密。
- scripts/tacita/run.py：协调两个进程，单个连续计时器贯穿KAHE/STE/最终解密，保留逐发送者记录。
- protocols/tacita/vendor、.cargo/config.toml：锁定的Cargo离线依赖，包括hopcroft-karp。
- scripts/setup、scripts/build、scripts/environment：系统依赖、Bazel/Cargo编译测试、磁盘内缓存。
- scripts/validation/validate_ste.py：L=32和L=10000真实STE+KAHE小规模验证；正式实验的硬门槛。
- plotting/paper.py、renderer.py：分指标作图、完整四协议图例及嵌入小图，不做数据平滑或估算总耗时。
- tests/paper、tests/layout、protocols/tacita/tests：统计、流程、路径保护、密码功能和恢复测试。

本机检查不替代新服务器上的Linux原生编译/冒烟；功能测试不构成安全证明或录用保证。
硬件环境以新run的manifest.json实测记录为准，不能照抄旧服务器型号。
