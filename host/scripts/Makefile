obj-m += pango_pcie_bar_test.o
obj-m += pango_pcie_small_dma_test.o
obj-m += pango_pcie_frame_dma_test.o
obj-m += pango_pcie_fpga_pattern_test.o
obj-m += pango_pcie_hdmi_status_test.o
obj-m += pango_pcie_hdmi_frame_test.o
obj-m += pango_pcie_multivideo.o

KDIR ?= $(HOME)/openharmony/out/kernel/OBJ/linux-5.10
LLVM_DIR ?= $(HOME)/openharmony/prebuilts/clang/ohos/linux-x86_64/15.0.4/llvm/bin
export PATH := $(LLVM_DIR):$(PATH)

.PHONY: all clean

all:
	$(MAKE) -C $(KDIR) M=$(CURDIR) ARCH=arm64 LLVM=1 LLVM_IAS=1 modules

clean:
	$(MAKE) -C $(KDIR) M=$(CURDIR) ARCH=arm64 LLVM=1 LLVM_IAS=1 clean
