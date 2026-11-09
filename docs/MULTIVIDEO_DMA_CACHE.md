# 双源视频性能优化：第 2 步，缓存型 DMA 缓冲

本步根据 `multivideo-profile-v4` 和驱动 `1.1` 的实机分段计时，优先减少 DMA 缓冲到内核记录的复制耗时。只更新驱动源码，继续使用已有 v4 服务、客户端和 FPGA 位流。

## 1. 本次实测与修改范围

内核第 4～30 帧，每路 27 个样本：

| 阶段 | HDMI 平均 | OV5640 平均 |
|---|---:|---:|
| `capture_us`，启动至检测完成 | 16.586 ms | 55.389 ms |
| `copy_us`，DMA 缓冲到内核记录 | 46.628 ms | 9.512 ms |
| `checksum_us`，设置部分头部及源 FNV | 20.586 ms | 7.551 ms |
| `record_us`，内核记录准备总计 | 85.674 ms | 72.657 ms |

服务第 10、20、30 帧的三个抽样平均值：用户态两路源 FNV 合计 24.162 ms，拼接 2.561 ms，输出 FNV 10.495 ms，提交 socket 4.467 ms，整轮 `total_us` 212.831 ms。服务抽样数量与内核不同，不应将这些平均值强行相加成同一个周期。驱动耗时包含在服务读头时间中，两层不能重复相加。本次附件没有 `.host.log`，这三个服务抽样的倒数不能代替客户端实收帧率。

原路径从 `dma_alloc_coherent` 缓冲读数据，在 RK3568 这类非一致性 DMA 平台上，CPU 访问可能使用不利于读取的内存属性。复制开销已经由实测确认，内存属性是否是主要原因需要本步对比验证。

| 项目 | 本步行为 |
|---|---|
| 驱动版本 | `1.2` |
| `cached_dma=0` | 默认值；使用原来的 coherent DMA 分配方式 |
| `cached_dma=1` | 尝试分配普通可缓存的物理连续页面并进行 streaming DMA 映射 |
| 缓存一致性 | 每次启动前 `dma_sync_single_for_device`，确认完成后 `dma_sync_single_for_cpu`；映射、同步、解除映射都使用完整缓冲区大小及 `DMA_BIDIRECTIONAL` |
| 分配失败 | 回落 coherent 缓冲并打印警告；实际模式以日志 `dma_buffer=` 为准 |
| 对比方法 | 比较 `sync_device_us + sync_cpu_us + copy_us` 以及 `record_us`，不能只看 `copy_us` |

由于保留 DMA 前的 CPU 填充值，映射方向使用 `DMA_BIDIRECTIONAL`。CPU 在交还给设备前填充缓冲，设备拥有期间不读写缓冲，确认完成并同步给 CPU 后才复制数据。释放时按实际分配方式解除映射并释放页面，或释放 coherent 缓冲。

继续保留内核源 FNV、用户态源 FNV、拼接输出 FNV、64 字节协议头、串行 DMA 调度和独占打开。这一步没有删除数据复制、改变分辨率、引入流水或实现以太网输入，也没有保证帧率达到某个值。

## 2. Ubuntu 同步、编译、签名

以下全部在 Ubuntu Bash 中执行。先关闭预览；此时板上的服务可以保持等待，编译成功后再停止它。

```bash
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"
export FPGA_BDF="0002:21:00.0"
export KDIR="$HOME/openharmony/out/kernel/OBJ/linux-5.10"
export LLVM_DIR="$HOME/openharmony/prebuilts/clang/ohos/linux-x86_64/15.0.4/llvm/bin"
export PATH="$LLVM_DIR:$PATH"
cd "$HOME/pcie_host"

CACHE_TAG="multivideo_cached_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$HOME/pcie_host/backups/$CACHE_TAG"
cp -p pango_pcie_multivideo.c pango_pcie_multivideo.ko \
    "$HOME/pcie_host/backups/$CACHE_TAG/"

sync_cached_dma_files()
{
    local filename
    for filename in pango_pcie_multivideo.c MULTIVIDEO_DMA_CACHE.md; do
        cp -v -- "/mnt/hgfs/pcie_host/$filename" "$HOME/pcie_host/" || return 1
        cmp -- "/mnt/hgfs/pcie_host/$filename" "$HOME/pcie_host/$filename" || return 1
    done
}

sync_cached_dma_files && \
make -C "$KDIR" M="$PWD" \
    ARCH=arm64 LLVM=1 LLVM_IAS=1 pango_pcie_multivideo.ko && \
"$KDIR/scripts/sign-file" sha1 \
    "$KDIR/certs/signing_key.pem" \
    "$KDIR/certs/signing_key.x509" \
    pango_pcie_multivideo.ko

modinfo -F version pango_pcie_multivideo.ko
modinfo -F vermagic pango_pcie_multivideo.ko
modinfo -F signer pango_pcie_multivideo.ko
```

必须同步、编译、签名全部成功，版本为 `1.2`、vermagic 对应板上 `5.10.184 ... aarch64`、签名者非空，再继续。共享目录不存在时不要继续使用旧文件。

## 3. 加载缓存型 DMA 驱动

```bash
sudo "$HDC" list targets
sudo "$HDC" -t "$TARGET" shell id
sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/pcie_multivideo_server --version
sudo "$HDC" -t "$TARGET" shell \
    sh /data/local/tmp/stop_multivideo_server.sh
sudo "$HDC" -t "$TARGET" shell rmmod pango_pcie_multivideo
sudo "$HDC" -t "$TARGET" shell lsmod
```

服务版本应为 `multivideo-profile-v4`。若板子刚重启、模块未加载，卸载提示不存在可以继续；若模块仍在使用，先停止采集进程，不能继续替换。

```bash
sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/pango_pcie_multivideo.ko" \
    /data/local/tmp/pango_pcie_multivideo.ko && \
sudo "$HDC" -t "$TARGET" shell \
    insmod /data/local/tmp/pango_pcie_multivideo.ko profile=1 cached_dma=1

sudo "$HDC" -t "$TARGET" shell \
    cat /sys/module/pango_pcie_multivideo/version
sudo "$HDC" -t "$TARGET" shell \
    cat /sys/module/pango_pcie_multivideo/parameters/cached_dma
sudo "$HDC" -t "$TARGET" shell \
    ls -l "/dev/pango_pcie_multivideo_$FPGA_BDF"
sudo "$HDC" -t "$TARGET" shell dmesg | \
    grep pango_pcie_multivideo | tail -n 10
```

应看到 `1.2`、`Y`、设备节点存在，并在最近一条 `dual-source video ready` 中看到 `dma_buffer=cached`。`Y` 只表示请求开启；若实际为 `coherent` 或出现回落警告，本次没有进入新路径，保存日志后先处理分配或映射原因，不能据此评价新路径收益。

## 4. 启动、测量、保存日志

```bash
STREAM_LOG="/data/local/tmp/${CACHE_TAG}.log"
sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/pcie_multivideo_server \
    --device "/dev/pango_pcie_multivideo_$FPGA_BDF" \
    --source both --serve --port 9002 \
    --send-timeout-ms 30000 --profile \
    --daemon --log "$STREAM_LOG"
sudo "$HDC" -t "$TARGET" shell tail -n 5 "$STREAM_LOG"

LOCAL_VIDEO_PORT=9004
sudo "$HDC" -t "$TARGET" fport "tcp:$LOCAL_VIDEO_PORT" tcp:9002
```

这里使用 Ubuntu `9004` 转发到 RK `9002`，避开此前占用的本地端口。必须看到 `Forwardport result:OK`。若 `9004` 也被占用，将 `LOCAL_VIDEO_PORT` 改为未占用的端口并重新创建转发，客户端复用同一变量。

```bash
/usr/bin/time -f 'HOST_TOTAL_SECONDS=%e' \
    python3 preview_multivideo.py \
    --host 127.0.0.1 --port "$LOCAL_VIDEO_PORT" \
    --headless --count 30 --timeout 60 \
    2>&1 | tee "$HOME/pcie_host/${CACHE_TAG}.host.log"
CLIENT_STATUS=${PIPESTATUS[0]}
printf 'CLIENT_EXIT=%s\n' "$CLIENT_STATUS"

sudo "$HDC" -t "$TARGET" shell cat "$STREAM_LOG" | \
    tee "$HOME/pcie_host/${CACHE_TAG}.server.log"
sudo "$HDC" -t "$TARGET" shell dmesg | \
    grep -E 'pango_pcie_multivideo.*(record_us=|record_delivery=|dma_buffer=|cached DMA)' | \
    tail -n 100 | tee "$HOME/pcie_host/${CACHE_TAG}.kernel.log"
```

确认客户端 `PASS`、退出码 `0`，然后使用同一个服务看动态图像：

```bash
python3 preview_multivideo.py \
    --host 127.0.0.1 --port "$LOCAL_VIDEO_PORT" --timeout 60
```

保持与基线相同的 HDMI 活动信号及输入内容。让 HDMI 有动态内容，同时移动摄像头前的物体，确认两路都更新且没有新花屏、冻结或错位。校验通过和序号递增不能独立证明没有缓存陈旧数据或像素位置错误。关闭预览后再进行其他采集。

将 `.host.log`、`.server.log`、`.kernel.log` 一起用于比较；内核旧日志可能仍在环形缓冲中，新路径行会包含 `dma_buffer=cached`。旧 `.ko` 的 `copy_us` 需要与新路径的复制及缓存同步合计比较，并检查 `record_us` 和实际接收耗时。保持计时开关一致后比较，正式验收关闭计时另测。

若出现 DMA 超时、`needs_reset`、协议描述错误或内核异常，保存日志并先恢复硬件状态。不能将这些情况当作普通内存分配失败反复卸载重试。30 帧客户端主动退出后，服务发送下一帧的 `Broken pipe` 需结合客户端是否已成功完成判断。

## 5. 正常状态下回退

`cached_dma` 是加载时参数，不能在采集中切换。正常采集结束后，若只想恢复原分配路径：

```bash
sudo "$HDC" -t "$TARGET" shell \
    sh /data/local/tmp/stop_multivideo_server.sh
sudo "$HDC" -t "$TARGET" shell rmmod pango_pcie_multivideo
sudo "$HDC" -t "$TARGET" shell \
    insmod /data/local/tmp/pango_pcie_multivideo.ko profile=1 cached_dma=0
sudo "$HDC" -t "$TARGET" shell dmesg | \
    grep pango_pcie_multivideo | tail -n 5
```

应看到 `dma_buffer=coherent`。用新的日志文件名重复第 4 节，可用同一版驱动进行 A/B 比较，既有的端口转发可复用。此回退只适用于没有 DMA 故障锁存的正常状态。

## 6. 本地验证边界

已审查 streaming DMA 的映射、CPU/设备所有权交接、失败回落和匹配释放路径；协议与服务未改动。`python -m unittest verify_multivideo.py` 的 9 项既有协议回归通过。当前 Windows 环境没有与板端匹配的 Linux 内核构建环境，未在本地生成新的 `.ko`；必须在 Ubuntu 完成上述内核编译和签名。协议测试不覆盖 DMA 缓存一致性，实际收益和像素正确性以 RK 实机测量及动态画面为准。
