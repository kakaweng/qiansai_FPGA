#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/fs.h>
#include <linux/io.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/pci.h>
#include <linux/slab.h>

#define PANGO_VENDOR_ID          0x0755
#define PANGO_DEVICE_ID          0x0755

#define FRAME_WIDTH              1920
#define FRAME_HEIGHT             1080
#define FRAME_SIZE               (FRAME_WIDTH * FRAME_HEIGHT)

#define BAR_STATUS               0
#define BAR_DMA_REGS             1
#define FRAME_STATUS_OFFSET      0x0
#define FRAME_DMA_ADDR_LOW       0x130
#define FRAME_DMA_ADDR_HIGH      0x140
#define FRAME_DMA_START          0x150
#define FRAME_STATUS_DONE        BIT(1)
#define FRAME_STATUS_ERROR       BIT(2)

struct pango_frame_dev {
	struct pci_dev *pdev;
	void __iomem *bar0;
	void __iomem *bar1;
	void *frame;
	dma_addr_t dma_addr;
	struct miscdevice miscdev;
};

static ssize_t pango_frame_read(struct file *file, char __user *buffer,
				 size_t count, loff_t *position)
{
	struct miscdevice *miscdev = file->private_data;
	struct pango_frame_dev *frame_dev =
		container_of(miscdev, struct pango_frame_dev, miscdev);

	return simple_read_from_buffer(buffer, count, position,
				       frame_dev->frame, FRAME_SIZE);
}

static const struct file_operations pango_frame_fops = {
	.owner = THIS_MODULE,
	.read = pango_frame_read,
	.llseek = default_llseek,
};

static int pango_wait_for_frame(struct pango_frame_dev *frame_dev)
{
	unsigned long timeout = jiffies + msecs_to_jiffies(10000);
	u8 *frame = frame_dev->frame;
	unsigned int index;

	msleep(100);
	do {
		dma_rmb();
		for (index = FRAME_SIZE - 64; index < FRAME_SIZE; index++) {
			if (READ_ONCE(frame[index]) != 0xa5)
				return 0;
		}
		msleep(10);
	} while (time_before(jiffies, timeout));

	dev_err(&frame_dev->pdev->dev,
		"frame DMA timed out; final 64 bytes were not written, FPGA status=%08x\n",
		readl(frame_dev->bar0 + FRAME_STATUS_OFFSET));
	return -ETIMEDOUT;
}

static int pango_probe(struct pci_dev *pdev,
		       const struct pci_device_id *id)
{
	struct pango_frame_dev *frame_dev;
	int ret;

	ret = pcim_enable_device(pdev);
	if (ret)
		return ret;

	ret = pcim_iomap_regions(pdev, BIT(BAR_STATUS) | BIT(BAR_DMA_REGS),
				 "pango_pcie_frame");
	if (ret)
		return ret;

	ret = dma_set_mask_and_coherent(&pdev->dev, DMA_BIT_MASK(64));
	if (ret) {
		ret = dma_set_mask_and_coherent(&pdev->dev, DMA_BIT_MASK(32));
		if (ret)
			return ret;
	}

	frame_dev = devm_kzalloc(&pdev->dev, sizeof(*frame_dev), GFP_KERNEL);
	if (!frame_dev)
		return -ENOMEM;

	frame_dev->pdev = pdev;
	frame_dev->bar0 = pcim_iomap_table(pdev)[BAR_STATUS];
	frame_dev->bar1 = pcim_iomap_table(pdev)[BAR_DMA_REGS];
	if (!frame_dev->bar0 || !frame_dev->bar1)
		return -ENODEV;

	frame_dev->frame = dma_alloc_coherent(&pdev->dev, FRAME_SIZE,
					       &frame_dev->dma_addr, GFP_KERNEL);
	if (!frame_dev->frame)
		return -ENOMEM;

	frame_dev->miscdev.minor = MISC_DYNAMIC_MINOR;
	frame_dev->miscdev.name = "pango_frame";
	frame_dev->miscdev.fops = &pango_frame_fops;

	ret = misc_register(&frame_dev->miscdev);
	if (ret)
		goto err_free_dma;

	pci_set_master(pdev);
	pci_set_drvdata(pdev, frame_dev);
	memset(frame_dev->frame, 0xa5, FRAME_SIZE);

	dev_info(&pdev->dev, "frame buffer DMA address: %pad, size: %u bytes\n",
		 &frame_dev->dma_addr, FRAME_SIZE);

	writel(lower_32_bits(frame_dev->dma_addr),
	       frame_dev->bar1 + FRAME_DMA_ADDR_LOW);
	writel(upper_32_bits(frame_dev->dma_addr),
	       frame_dev->bar1 + FRAME_DMA_ADDR_HIGH);
	wmb();
	writel(1, frame_dev->bar1 + FRAME_DMA_START);

	ret = pango_wait_for_frame(frame_dev);
	if (ret)
		goto err_deregister_misc;

	dma_rmb();
	dev_info(&pdev->dev,
		 "single-frame DMA passed: %u-byte 1920x1080 grayscale frame received\n",
		 FRAME_SIZE);
	print_hex_dump(KERN_INFO, "pango-frame: ", DUMP_PREFIX_OFFSET,
		       16, 1, frame_dev->frame, 64, false);
	return 0;

err_deregister_misc:
	misc_deregister(&frame_dev->miscdev);
err_free_dma:
	dma_free_coherent(&pdev->dev, FRAME_SIZE,
			  frame_dev->frame, frame_dev->dma_addr);
	frame_dev->frame = NULL;
	return ret;
}

static void pango_remove(struct pci_dev *pdev)
{
	struct pango_frame_dev *frame_dev = pci_get_drvdata(pdev);

	if (frame_dev && frame_dev->frame) {
		misc_deregister(&frame_dev->miscdev);
		dma_free_coherent(&pdev->dev, FRAME_SIZE,
				  frame_dev->frame, frame_dev->dma_addr);
	}
	pci_clear_master(pdev);
}

static const struct pci_device_id pango_ids[] = {
	{ PCI_DEVICE(PANGO_VENDOR_ID, PANGO_DEVICE_ID) },
	{ }
};
MODULE_DEVICE_TABLE(pci, pango_ids);

static struct pci_driver pango_driver = {
	.name = "pango_pcie_frame",
	.id_table = pango_ids,
	.probe = pango_probe,
	.remove = pango_remove,
};
module_pci_driver(pango_driver);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Pango PCIe 1920x1080 grayscale frame receiver");
MODULE_AUTHOR("OpenAI Codex");
