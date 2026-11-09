# 恢复增加第二路 OV5640 之前的版本

电脑上的 FPGA 工程和 RK/主机源码已从 `dual_camera_baseline_20261025` 恢复。本版包含 HDMI、一颗 OV5640、主机 Ethernet 输入，拼接继续在 RK3568 上完成。

| 项目 | 恢复目标 |
|---|---|
| FPGA 下载文件 | `F:/B_file/jichuangsai/Pango_FPGA/06_hdmi_loop_1/hdmi_loop.ethernet_video_v1.sbit` |
| 默认下载输出 | `06_hdmi_loop_1/generate_bitstream/hdmi_loop.sbit`，已复制同一旧位流 |
| 驱动源码 | `pango_pcie_multivideo.c`，模块版本 `1.3` |
| 服务程序 | `pcie_multivideo_server.ethernet_v6`，版本 `multivideo-ethernet-v6` |
| 三路服务参数 | `--source all --overlap-verify` |
| 预览 | `preview_multivideo.py`，Ubuntu 本地 9004 转发到 RK 9002 |
| 画面 | 左侧 HDMI、右上第一摄像头、右下 Ethernet；1280×480，source=4294967294 |

双摄代码、v7 程序、位流和生成目录保存在 `F:/B_file/jichuangsai/Pango_FPGA/dual_camera_v7_saved_20261025_203558`，其中 `rollback_manifest.json` 记录了恢复文件的 SHA256。未删除这些备份。PDS 如果开着双摄工程，关闭时不要保存旧窗口状态，再重新打开恢复后的 `hdmi_loop.pds`。

## 1. FPGA 下载与 RK 重启

在 PDS 下载 `hdmi_loop.ethernet_video_v1.sbit`，确认下载成功，保持 FPGA 供电。若 FPGA 断电导致 SRAM 配置丢失，需要重新下载这个文件。

Ubuntu 终端 A：

```bash
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"
export FPGA_BDF="0002:21:00.0"
export KDIR="$HOME/openharmony/out/kernel/OBJ/linux-5.10"
export LLVM_DIR="$HOME/openharmony/prebuilts/clang/ohos/linux-x86_64/15.0.4/llvm/bin"
export PATH="$LLVM_DIR:$PATH"
cd "$HOME/pcie_host"

sudo "$HDC" -t "$TARGET" shell reboot
```

等待板子启动完成后，再执行：

```bash
sudo "$HDC" list targets
sudo "$HDC" -t "$TARGET" shell id
sudo "$HDC" -t "$TARGET" shell ls /sys/bus/pci/devices
sudo "$HDC" -t "$TARGET" shell \
    ls -l "/sys/bus/pci/devices/$FPGA_BDF/resource0"
```

只有看到 FPGA 的 PCIe 设备和 `resource0` 后，才继续部署。当前 PCIe 枚举故障是否恢复需要实物检查；替换主机源码或加载视频模块不能创建尚未枚举的设备。

## 2. 终端 A：同步已恢复的源码和 v6 程序

```bash
ls -lh /mnt/hgfs/pcie_host/pcie_multivideo_server.ethernet_v6 \
    /mnt/hgfs/pcie_host/pango_pcie_multivideo.c
```

共享目录不存在时先恢复挂载：

```bash
sudo mkdir -p /mnt/hgfs
if ! mountpoint -q /mnt/hgfs; then
    sudo vmhgfs-fuse .host:/ /mnt/hgfs -o allow_other
fi
```

然后同步，避免 Ubuntu 继续使用之前复制的双摄源码：

```bash
sync_single_camera_v6()
{
    local filename
    for filename in Makefile pango_pcie_multivideo.c pcie_multivideo_protocol.h \
        pcie_multivideo_server.c video_mosaic.c video_mosaic.h build_multivideo.sh \
        pcie_multivideo_server.ethernet_v6 pcie_ethernet_status pcie_ethernet_status.c \
        preview_multivideo.py multivideo_stream.py frame_to_png.py frame_format.py \
        send_ethernet_video.py check_multivideo_cpu.sh stop_multivideo_server.sh \
        ETHERNET_VIDEO.md ROLLBACK_SINGLE_CAMERA.md; do
        cp -v -- "/mnt/hgfs/pcie_host/$filename" "$HOME/pcie_host/" || return 1
        cmp -- "/mnt/hgfs/pcie_host/$filename" "$HOME/pcie_host/$filename" || return 1
    done
}
sync_single_camera_v6
```

## 3. 终端 A：重新编译并签名 1.3 驱动

```bash
if [ -f pango_pcie_multivideo.ko ]; then
    cp -v -- pango_pcie_multivideo.ko \
        "pango_pcie_multivideo.before_rollback_$(date +%Y%m%d_%H%M%S).ko"
fi

make -C "$KDIR" M="$PWD" ARCH=arm64 LLVM=1 LLVM_IAS=1 \
    pango_pcie_multivideo.ko && \
"$KDIR/scripts/sign-file" sha1 \
    "$KDIR/certs/signing_key.pem" "$KDIR/certs/signing_key.x509" \
    pango_pcie_multivideo.ko

printf 'BUILD_SIGN_EXIT=%s\n' "$?"
```

确认 `BUILD_SIGN_EXIT=0` 再继续。

## 4. 终端 A：部署旧版配套文件

```bash
sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/stop_multivideo_server.sh" /data/local/tmp/stop_multivideo_server.sh
sudo "$HDC" -t "$TARGET" shell sh /data/local/tmp/stop_multivideo_server.sh
sudo "$HDC" -t "$TARGET" shell lsmod
```

刚重启且模块列表为空，无需卸载。若有 `pango_pcie_multivideo`，先执行一次：

```bash
sudo "$HDC" -t "$TARGET" shell rmmod pango_pcie_multivideo
```

再执行：

```bash
sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/pango_pcie_multivideo.ko" /data/local/tmp/pango_pcie_multivideo.ko
sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/pcie_multivideo_server.ethernet_v6" \
    /data/local/tmp/pcie_multivideo_server.ethernet_v6
sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/check_multivideo_cpu.sh" /data/local/tmp/check_multivideo_cpu.sh
sudo "$HDC" -t "$TARGET" shell chmod 755 \
    /data/local/tmp/pcie_multivideo_server.ethernet_v6

sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/pcie_multivideo_server.ethernet_v6 --version
sudo "$HDC" -t "$TARGET" shell \
    insmod /data/local/tmp/pango_pcie_multivideo.ko profile=1 cached_dma=1
sudo "$HDC" -t "$TARGET" shell cat /sys/module/pango_pcie_multivideo/version
sudo "$HDC" -t "$TARGET" shell ls -l "/dev/pango_pcie_multivideo_$FPGA_BDF"
```

预期服务版本 `multivideo-ethernet-v6`、驱动版本 `1.3`、字符设备存在。遇到 HDC 上传失败，恢复连接后重新上传失败文件，再继续。

## 5. Windows PowerShell：以太网输入

```powershell
Set-Location 'F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host'
python .\send_ethernet_video.py --host 192.168.1.10 --bind 192.168.1.102 --fps 5 --packet-gap-us 100 --animate
```

沿用已验证的网线和网卡地址，保持此终端运行。

## 6. 终端 A：启动恢复后的三路服务

```bash
STREAM_LOG="/data/local/tmp/single_camera_v6_$(date +%Y%m%d_%H%M%S).log"
sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/pcie_multivideo_server.ethernet_v6 \
    --device "/dev/pango_pcie_multivideo_$FPGA_BDF" \
    --source all --overlap-verify --serve --port 9002 \
    --send-timeout-ms 30000 --profile --daemon --log "$STREAM_LOG"
sudo "$HDC" -t "$TARGET" shell tail -n 10 "$STREAM_LOG"
sudo "$HDC" -t "$TARGET" fport ls
ss -ltn 'sport = :9004'
```

没有 9004→9002 映射且 9004 空闲时，建立转发：

```bash
sudo "$HDC" -t "$TARGET" fport tcp:9004 tcp:9002
```

已有正确映射时直接复用。

## 7. Ubuntu 终端 B：临时 CPU performance

```bash
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"
sudo "$HDC" -t "$TARGET" shell sh /data/local/tmp/check_multivideo_cpu.sh hold 300
```

看到 `CPU_HOLD_READY` 后保持 B 运行，回到 A。结束后 B 应输出 `CPU_GOVERNORS_RESTORED`。

## 8. 终端 A：检查与预览

```bash
/usr/bin/time -f 'TRIPLE_HOST_SECONDS=%e' \
    python3 preview_multivideo.py --host 127.0.0.1 --port 9004 \
    --headless --count 30 --timeout 60
```

预期 source=4294967294、1280×480，最后 `PASS: 已接收并校验 30 帧`。通过后打开窗口：

```bash
python3 preview_multivideo.py --host 127.0.0.1 --port 9004 --timeout 60
```

出现问题时在同一终端 A 取日志：

```bash
sudo "$HDC" -t "$TARGET" shell tail -n 80 "$STREAM_LOG"
```

## 本地回退核验

| 核验 | 结果 |
|---|---|
| 22 个恢复文件对原备份逐文件 SHA256 | 一致 |
| 原三路位流 SHA256 | `58e2c6b17e13a282ffb67b8908568088ce78aa04bf16b49f1bb070f670774626` |
| 原 v6 服务 SHA256 | `d28439094128866ac04273379fddc9ac1c817c1c9d3426f07d25b01787dd99a8` |
| Python 协议现有 9 项测试 | 通过 |
| 三路画布逐像素测试 | 通过 |
| 原 BAR0 描述与 DMA 读取选择仿真 | 通过 |

此次直接恢复原已验证位流，没有重新综合生成位流。双摄编译中间目录已归档，后续重新构建会从恢复后的源码重新生成。实物回退、PCIe 枚举及预览恢复情况须在用户板卡上确认。
