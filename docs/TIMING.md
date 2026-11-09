# 每帧耗时测量：新一天从编译到预览

当前驱动在 DMA 完成后，先将整帧复制到可缓存的普通内存，再执行像素扫描和
FNV 校验。保留缓冲填充、像素扫描、三端 FNV 校验和 DMA 流程。第一轮基准和
本次优化的验证目标见文末“第一轮结果与驱动优化”。
FPGA 位流沿用之前验证通过的版本。需要重新编译并签名内核模块；共享目录中的
用户空间发送程序已按新源码交叉编译，复制即可。新计时默认关闭，按下面参数启用。

| 部分 | 启用方式 | 输出位置 |
| --- | --- | --- |
| RK3568 内核驱动 | `insmod ... profile=1` | `dmesg` 中的 `HDMI timing:` |
| RK3568 发送程序 | `pcie_stream_server --daemon --profile --log 路径` | 板端日志中的 `Server timing:` |
| Ubuntu 接收程序 | `preview_stream.py --profile` | Ubuntu 终端中的 `Preview timing:` |
| Ubuntu 窗口 | 同上，运行窗口模式 | Ubuntu 终端中的 `Display timing:` |

计时以 `us`（微秒）输出：`1000 us = 1 ms`。使用单调时钟，包含该段执行期间的
调度等待。前三帧及之后序号为 10 的倍数的帧打印采样日志；显示端只打印实际显示
的新帧。日志和计时本身也有开销，因此这是保留现有验证操作的基准测量。

## 1. 终端 A：初始化并挂载共享目录

在 Ubuntu 中执行。新终端的变量需要重新设置；Windows 共享目录应指向
`F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host`。

```bash
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"
export KDIR="$HOME/openharmony/out/kernel/OBJ/linux-5.10"
export LLVM_DIR="$HOME/openharmony/prebuilts/clang/ohos/linux-x86_64/15.0.4/llvm/bin"

sudo mkdir -p /mnt/hgfs
if ! mountpoint -q /mnt/hgfs; then
    sudo vmhgfs-fuse .host:/ /mnt/hgfs -o allow_other
fi

ls -l /mnt/hgfs/pcie_host/pango_pcie_hdmi_frame_test.c \
    /mnt/hgfs/pcie_host/pcie_stream_server \
    /mnt/hgfs/pcie_host/preview_stream.py
```

如果 `ls` 找不到这些文件，先恢复 VMware 共享挂载，不继续编译旧源码。

## 2. 同步、编译并签名

```bash
mkdir -p "$HOME/pcie_host"
cp -v /mnt/hgfs/pcie_host/Makefile \
    /mnt/hgfs/pcie_host/pango_pcie_hdmi_frame_test.c \
    /mnt/hgfs/pcie_host/pcie_stream_server.c \
    /mnt/hgfs/pcie_host/pcie_stream_server \
    /mnt/hgfs/pcie_host/preview_stream.py \
    /mnt/hgfs/pcie_host/rknn_stream.py \
    /mnt/hgfs/pcie_host/draw_detections.py \
    /mnt/hgfs/pcie_host/capture_stream.py \
    /mnt/hgfs/pcie_host/frame_to_png.py \
    /mnt/hgfs/pcie_host/frame_format.py \
    /mnt/hgfs/pcie_host/frame_checksum.c \
    /mnt/hgfs/pcie_host/build_checksum.sh \
    /mnt/hgfs/pcie_host/TIMING.md \
    "$HOME/pcie_host/"

cd "$HOME/pcie_host"
bash build_checksum.sh
grep -n 'HDMI timing:' pango_pcie_hdmi_frame_test.c
grep -n 'Preview timing:' preview_stream.py

make KDIR="$KDIR" LLVM_DIR="$LLVM_DIR" && \
    "$KDIR/scripts/sign-file" sha1 \
    "$KDIR/certs/signing_key.pem" \
    "$KDIR/certs/signing_key.x509" \
    pango_pcie_hdmi_frame_test.ko

modinfo -F vermagic pango_pcie_hdmi_frame_test.ko
modinfo -F signer pango_pcie_hdmi_frame_test.ko
modinfo -p pango_pcie_hdmi_frame_test.ko
file pcie_stream_server
```

新 `.ko` 的参数列表必须包含 `profile`，签名者不能为空，vermagic 应与板端
`5.10.184 ... aarch64` 匹配。编译或签名失败时先停在这一步，不发送旧 `.ko`。
`pcie_stream_server` 是 ARM64 静态用户空间程序，不需要内核模块签名。

Makefile 仍会构建此前的测试模块，沿用现有构建方式。内核目录缺少 `Module.symvers`
的原有警告不等于本次计时代码编译失败，但必须确认编译命令确实成功结束。

## 3. 停止旧预览并卸载旧模块

先在预览窗口按 `Q` 退出，再停止板端发送程序：

```bash
sudo "$HDC" list targets
sudo "$HDC" -t "$TARGET" shell uname -r
sudo "$HDC" -t "$TARGET" shell \
    'if pidof pcie_stream_server >/dev/null; then killall pcie_stream_server; fi'
sudo "$HDC" -t "$TARGET" shell pidof pcie_stream_server
sudo "$HDC" -t "$TARGET" shell lsmod | grep pango_pcie
```

`pidof` 没有输出后继续。如果模块列表包含 `pango_pcie_hdmi_frame_test`，卸载它：

```bash
sudo "$HDC" -t "$TARGET" shell rmmod pango_pcie_hdmi_frame_test
```

如果 RK3568 已重启且模块列表没有它，跳过卸载。如果提示模块正在使用，先关闭
预览和其他读取 stream 设备的程序，不强制卸载。其他 PCIe 测试模块占用设备时，
也要先卸载列表中实际存在的对应测试模块。

## 4. 发送并加载测量版本

```bash
sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/pango_pcie_hdmi_frame_test.ko" \
    /data/local/tmp/pango_pcie_hdmi_frame_test.ko
sudo "$HDC" -t "$TARGET" shell insmod \
    /data/local/tmp/pango_pcie_hdmi_frame_test.ko profile=1

sudo "$HDC" -t "$TARGET" shell cat \
    /sys/module/pango_pcie_hdmi_frame_test/parameters/profile
sudo "$HDC" -t "$TARGET" shell ls -l \
    /dev/pango_pcie_stream_0002:21:00.0
sudo "$HDC" -t "$TARGET" shell dmesg | \
    grep -E 'HDMI timing:|HDMI stream capture ready|probe of' | tail -n 8

sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/pcie_stream_server" \
    /data/local/tmp/pcie_stream_server
sudo "$HDC" -t "$TARGET" shell chmod 755 /data/local/tmp/pcie_stream_server
sudo "$HDC" -t "$TARGET" shell /data/local/tmp/pcie_stream_server --help
```

参数应显示 `Y`，stream 节点应存在，发送程序帮助应包含 `--profile`。驱动加载时
的首帧也会打印一条计时，后续重点比较持续采集的 `seq=10/20/30` 等样本。

## 5. 后台启动板端测量和 USB 转发

使用这次测量独立的日志文件，避免混入昨天的记录：

```bash
PROFILE_TAG="pcie_timing_$(date +%Y%m%d_%H%M%S)"
REMOTE_PROFILE_LOG="/data/local/tmp/${PROFILE_TAG}_server.log"

sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/pcie_stream_server --daemon --profile --log "$REMOTE_PROFILE_LOG"
sudo "$HDC" -t "$TARGET" shell pidof pcie_stream_server
sudo "$HDC" -t "$TARGET" shell tail -n 10 "$REMOTE_PROFILE_LOG"
sudo "$HDC" -t "$TARGET" fport tcp:9000 tcp:9000

printf '本次板端日志：%s\n' "$REMOTE_PROFILE_LOG"
```

启动日志应包含 `Server profiling enabled`。等待终端 B 连接时没有逐帧日志是正常的。
测量期间仅打开一个预览客户端，不同时执行 `dd` 或其他 stream 读取程序。

## 6. 终端 B：显示并测量 30 帧

在 Ubuntu 图形桌面另开终端：

```bash
cd "$HOME/pcie_host"
LOCAL_PREVIEW_LOG="$HOME/pcie_host/preview_timing_$(date +%Y%m%d_%H%M%S).log"
set -o pipefail
python3 -u preview_stream.py --profile --count 30 2>&1 | tee "$LOCAL_PREVIEW_LOG"
printf '本次窗口日志：%s\n' "$LOCAL_PREVIEW_LOG"
```

正常接收 30 帧后窗口自动关闭；这是 `--count 30` 的行为。需要持续预览时去掉
`--count 30`。如果断线，保留重连日志；不要把有重连的样本当作稳定频率基准。

截图和保存文件会引入额外开销，基准测量期间不按 `S` 保存截图。

## 7. 收集并反馈三端计时

回到终端 A（保留步骤 5 的变量）：

```bash
sudo "$HDC" -t "$TARGET" shell dmesg > "$HOME/pcie_host/${PROFILE_TAG}_kernel.log"
grep 'HDMI timing:' "$HOME/pcie_host/${PROFILE_TAG}_kernel.log" | tail -n 8

sudo "$HDC" -t "$TARGET" shell tail -n 40 "$REMOTE_PROFILE_LOG"
sudo "$HDC" -t "$TARGET" file recv \
    "$REMOTE_PROFILE_LOG" "$HOME/pcie_host/${PROFILE_TAG}_server.log"
```

在终端 B 汇总接收和显示计时：

```bash
grep -E 'Preview timing:|Display timing:' "$LOCAL_PREVIEW_LOG" | tail -n 16
```

反馈三种输出即可：`HDMI timing`、`Server timing`、`Preview timing / Display timing`。
尽量保留相同 `seq` 的采样行，可以把一帧在各处的处理对应起来。

## 字段解释

### 内核：HDMI timing

| 字段 | 测量范围 |
| --- | --- |
| `seq` | 本次驱动绑定期间的成功采集序号 |
| `lock_us` | 获取采集互斥锁的等待时间 |
| `check_us` | 检查设备状态和 BAR0 |
| `fill_us` | 将 2,073,600 字节 DMA 缓冲填充为 `0xa5` |
| `setup_us` | 设置 Bus Master、屏障、写 DMA 地址及 MMIO 刷新 |
| `submit_us` | 写启动寄存器、刷新并读取提交状态 |
| `start_log_us` | 原有启动日志及 debug 寄存器读取/打印 |
| `wait_us` | 轮询到 DMA 完成，包含轮询休眠及调度；不是纯 PCIe 总线传输时间 |
| `dma_copy_us` | DMA 完成后的屏障及整帧复制：从 coherent DMA 缓冲复制到可缓存的普通内存 |
| `changed_us` | 遍历普通内存中的帧副本，统计不同于 `0xa5` 的像素数 |
| `checksum_us` | 对普通内存中的帧副本逐字节计算 FNV-1a |
| `copy_meta_us` | 可选的像素副本复制和序号、时间戳等元数据更新；stream 路径通常无该像素副本 |
| `finish_us` | 原有完成日志和清除 Bus Master 等收尾 |
| `total_us` | 从进入采集函数到收尾的合计，不含新增 timing 日志打印及之后的解锁 |
| `interval_us` | 相邻完整帧记录的完成时间戳间隔，包含函数外的等待/空闲 |

除 `interval_us` 外，各阶段加起来应接近 `total_us`，微秒取整可能产生小差异。
原有 `capture_us` 约等于 `submit_us + start_log_us + wait_us`，不包含填充和校验。
首帧 `interval_us=0`；新连接之前若闲置了一段时间，新连接首帧间隔可能很大，
不要用于持续采集性能判断。轮询仍沿用原来的 20 ms 休眠。

### 板端用户程序：Server timing

| 字段 | 测量范围 |
| --- | --- |
| `read_us` | 等待 stream 记录就绪和将记录读入用户空间；包含内核等待及数据复制 |
| `checksum_us` | 用户程序检查帧头并重新计算 FNV-1a |
| `send_us` | 循环 send 直到该记录被本地 TCP 栈接受，可能包含发送缓冲反压 |
| `total_us` | 本次读取、校验和提交发送的合计，不包含后续日志打印 |
| `interval_us` | 相邻记录完成 send 的时间间隔 |

`send_us` 不是 Ubuntu 已收到整帧的时间；TCP 可能仍在传输。内核在后台持续采集，
若环已提前准备帧，`read_us` 可能很小；慢客户端反压会反过来影响环的采集节奏。

### Ubuntu：Preview timing / Display timing

| 字段 | 测量范围 |
| --- | --- |
| `header_us` | 等待 40 字节帧头、分配/读取缓冲并检查元数据 |
| `pixels_us` | 接收像素和转换为 bytes；包含等待发送端和 HDC/USB 传输 |
| `checksum_us` | Python 循环计算 2,073,600 字节 FNV-1a |
| `total_us` | 本次帧头、像素接收和校验合计，不含发布到窗口及日志打印 |
| `interval_us` | 相邻两帧接收和校验完成的间隔，不跨重连计算 |
| `render_us` | 窗口取帧、缩放、灰度转显示图像、叠加文字并调用 imshow |
| `gui_wait_us` | 一次 waitKey(30) 的事件处理和等待，包含指定的等待时间 |

`render_us` 是主线程的一次更新开销，不是像素已经出现在屏幕的精确延迟。
窗口仍会在帧间刷新状态文字，显示计时只采样新帧的第一次更新。

三端处于流水线中，耗时会重叠，不把三端 `total_us` 直接相加作为每帧时间，也
不能用两台机器的单调时钟绝对值相减求端到端延迟。`interval_us` 比较持续速率，
各阶段耗时用于找出主要 CPU 开销、等待和反压位置。

## 可选：确认窗口本身是否影响接收

仅当窗口处理耗时较大或需要比较时，完成上述 30 帧后再运行一次无窗口检查：

```bash
cd "$HOME/pcie_host"
python3 -u preview_stream.py --headless --profile --count 30
```

比较持续采样中的 `interval_us` 和 Python `checksum_us`；不要用连接起始时的
缓冲突发或跨连接空闲间隔计算速率。

## 第一轮结果与驱动优化

用户上板测得的持续样本 `seq=10/20/30` 均值：

| 部分 | 阶段 | 耗时 |
| --- | --- | --- |
| 驱动 | 等待完成 `wait_us` | 24.18 ms |
| 驱动 | 扫描变化字节 `changed_us` | 247.83 ms |
| 驱动 | FNV 校验 `checksum_us` | 241.33 ms |
| 驱动 | 采集调用 `total_us` | 513.84 ms |
| 板端发送程序 | 用户空间 FNV 校验 | 5.16 ms |
| 板端发送程序 | TCP 提交 | 3.21 ms |
| Ubuntu | Python FNV 校验 | 194.38 ms |
| Ubuntu | 一次窗口绘制 | 0.85 ms |

驱动的两次扫描合计约 489.17 ms，占采集调用时间的 95.2%。同算法在板端普通
用户空间缓冲上的校验只需约 5 ms，结合驱动直接逐字节读取 `dma_alloc_coherent`
缓冲的代码，支持“DMA 缓冲的 CPU 读取方式造成主要开销”的判断。实际映射属性
仍由 RK3568 内核的 DMA 实现决定，不能仅凭时间断言页属性。

本次驱动增加一个 2,073,600 字节的普通内存缓冲，加载时一次分配，随设备上下文
释放；重复打开 stream 时继续复用。采集互斥锁保护其整帧复制、扫描、校验和可选
图像副本复制。DMA 缓冲、设备收到的 DMA 地址、四槽队列及 40 字节帧头格式沿用
现有设计。每帧继续执行变化检查和相同的 FNV 校验，发送程序及 Python 无需更新。

按本文流程重新编译、签名、卸载及加载驱动，使用 `profile=1` 再预览 30 帧。
确认加载日志出现 `HDMI cached validation buffer ready`，持续采样出现
`dma_copy_us`，然后比较：

| 验证项 | 判断方法 |
| --- | --- |
| 图像与完整性 | 图像内容正常，发送端与接收端校验通过 |
| 驱动优化效果 | 比较新的 `dma_copy_us + changed_us + checksum_us` 与旧的 489.17 ms |
| 采集速度 | 比较持续样本的驱动 `total_us` 和帧完成间隔 |
| 新的限制 | 如果驱动明显加快、窗口仍慢，继续核对 Python 校验与 TCP/HDC 传输 |

队列被慢消费者填满时，驱动的 `interval_us` 还包含等待空槽；此时应结合
`total_us` 判断采集函数本身的速度。驱动缓存副本优化的实测结果及当前 Python
加速方案见下一节。新的实际 FPS 以用户上板结果为准。

## 第二轮结果与 Ubuntu 校验加速

缓存副本版本的驱动和发送程序持续样本 `seq=40/50/.../130` 均值：

| 阶段 | 耗时 |
| --- | --- |
| 驱动完成等待 | 25.04 ms |
| DMA 缓冲复制到普通内存 | 19.02 ms |
| 普通内存变化扫描 | 4.40 ms |
| 普通内存 FNV 校验 | 5.46 ms |
| 驱动采集调用总时间 | 54.48 ms |
| 板端发送循环总时间 | 54.78 ms |

驱动调用约比旧版本快 9.43 倍，对应持续采集约 18 帧/秒。该轮 Ubuntu 接收端
`seq=10/20/30` 的整帧校验平均 242.00 ms，完成间隔平均 242.71 ms，约 4.12
帧/秒，校验占接收处理耗时 99.7%。这些是不同序号区间的持续样本，不能当成同帧
时延相加。接收速度低于发送速度时，TCP/HDC 中的缓冲会使画面排队。

本次增加 `frame_checksum.c` 和 `build_checksum.sh`。C 库继续逐字节计算相同的
32 位 FNV-1a，每帧继续核对驱动提供的校验值。Python 通过 `ctypes.CDLL` 调用 C
函数，读取 `bytes` 或 `bytearray` 时不创建整帧副本；C 调用期间释放 GIL，窗口
线程可继续处理图像和事件。库缺失或无法加载时回退原 Python 算法。

本轮只同步以下 Ubuntu 文件：

```bash
cp -v /mnt/hgfs/pcie_host/frame_to_png.py \
    /mnt/hgfs/pcie_host/frame_format.py \
    /mnt/hgfs/pcie_host/preview_stream.py \
    /mnt/hgfs/pcie_host/rknn_stream.py \
    /mnt/hgfs/pcie_host/draw_detections.py \
    /mnt/hgfs/pcie_host/frame_checksum.c \
    /mnt/hgfs/pcie_host/build_checksum.sh \
    /mnt/hgfs/pcie_host/TIMING.md \
    "$HOME/pcie_host/"
cd "$HOME/pcie_host"
bash build_checksum.sh
python3 -c 'from frame_to_png import checksum_backend; print(checksum_backend())'
```

应输出 `C/FNV-1a`。构建时使用 Ubuntu 本机的 `cc`，不使用 ARM64 交叉编译器；
这份库只供 Ubuntu Python 加载，驱动和板端程序继续使用缓存副本版本。

关闭旧窗口，按本文第 3 节的停止发送程序命令结束旧 TCP 连接，确认 `pidof`
无输出。使用新的日志路径按第 5 节重新启动发送程序，沿用已有端口转发；本轮
跳过驱动卸载、编译签名和加载。然后按第 6 节带 `--profile --count 30` 预览，
确认启动显示 `校验后端：C/FNV-1a`，比较持续样本的 `checksum_us`、
`interval_us`、窗口 Capture/Receive FPS 和序号跳过数量。

Python 加速后的新 FPS、窗口耗时以及传输限制仍以用户实测为准。

## 第三轮结果：C 校验已上板验证

本轮接收端采样序号从 139 开始。内核日志中的 `seq=80/90/.../130` 属于上一轮，
不用于本轮统计。排除启动阶段，当前驱动 `seq=150/160/170` 和接收端
`seq=150/160` 的样本均值如下：

| 阶段 | 耗时 |
| --- | --- |
| 驱动完成等待 | 25.09 ms |
| DMA 缓冲复制到普通内存 | 19.07 ms |
| 普通内存变化扫描 | 4.28 ms |
| 普通内存 FNV 校验 | 5.33 ms |
| 驱动采集调用总时间 | 54.31 ms |
| Ubuntu 像素接收 | 41.71 ms |
| Ubuntu C/FNV 校验 | 2.99 ms |
| Ubuntu 窗口绘制 | 0.75 ms |
| Ubuntu 窗口事件处理与等待 | 31.34 ms |

Ubuntu 校验从上一轮约 242 ms 降到约 3 ms，约加快 81 倍。当前样本中，接收和
校验可以跟上采集；驱动 `seq=150` 到 `seq=170` 的完整 20 个帧间隔约为
1.120559 秒，对应约 17.85 帧/秒。短时间接收间隔可能受 TCP/HDC 缓冲和调度影响，
不能将某一帧的瞬时 Receive FPS 当作长期采集速度。

`seq=139` 的 `header_us` 和发送端 `read_us` 包含连接启动等待，不用于稳态判断。
日志只打印前三帧及十的倍数序号，`141 -> 150` 的采样跳跃不表示丢帧；应看完整
接收输出中的“序号跳过”和窗口的 `Display skips`。

当前驱动等待函数仍使用 `msleep(20)` 轮询，`wait_us` 中包含休眠及调度等待，
不能将约 25 ms 认定为纯 PCIe DMA 时间。后续若继续优化采集速度，可单独调整
轮询周期，再比较 `wait_us` 和 `total_us`；当前版本继续作为 RKNN 接入的采集基准。

保持当前驱动和发送程序运行，持续预览可使用：

```bash
cd "$HOME/pcie_host"
python3 -u preview_stream.py
```

下一阶段在 RK3568 上直接读取 stream 帧，按模型要求预处理后调用 RKNN/NPU，
再将帧序号及识别结果提供给预览端。接入前需要确认已跑通的识别命令、模型路径、
RKNN 运行库和源码位置。识别速度慢于采集时，应使用持续读取与最新帧处理的方式，
避免历史帧积压。

## 测完恢复正常预览

新版本按原流程运行仍可使用。下次加载驱动时省略 `profile=1`，启动发送程序和
窗口时省略 `--profile`，即可关闭新增测量日志。驱动运行时也可关闭计时：

```bash
sudo "$HDC" -t "$TARGET" shell \
    'echo 0 > /sys/module/pango_pcie_hdmi_frame_test/parameters/profile'
```
