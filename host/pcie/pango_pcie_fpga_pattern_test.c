#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/jiffies.h>
#include <linux/module.h>
#include <linux/pci.h>
#include <linux/string.h>

#define PANGO_VENDOR_ID 0x0755
#define PANGO_DEVICE_ID 0x0755
#define PANGO_BAR0 0
#define PANGO_BAR1 1

#define FRAME_WIDTH 64
#define FRAME_HEIGHT 64
#define FRAME_BYTES (FRAME_WIDTH * FRAME_HEIGHT)
#define CHUNK_BYTES 256
#define DMA_TIMEOUT_MS 2000
#define DMA_ADDR_LOW 0x110
#define DMA_COMMAND 0x100
#define PATTERN_COMMAND 0x180
#define PATTERN_MAGIC 0x5041544e
#define DMA_COMMAND_WRITE (0x01000000 | ((CHUNK_BYTES / 4) - 1))

static u8 pango_expected_byte(size_t offset)
{
	return (offset / 16) % 16;
}

static bool pango_chunk_matches(const u8 *destination, size_t offset)
{
	size_t index;

	for (index = offset; index < offset + CHUNK_BYTES; index++) {
		if (READ_ONCE(destination[index]) != pango_expected_byte(index))
			return false;
	}

	return true;
}

static int pango_wait_for_chunk(struct pci_dev *pdev, const u8 *destination,
				size_t offset)
{
	unsigned long timeout = jiffies + msecs_to_jiffies(DMA_TIMEOUT_MS);
	size_t index;

	do {
		dma_rmb();
		if (pango_chunk_matches(destination, offset))
			return 0;
		msleep(10);
	} while (time_before(jiffies, timeout));

	for (index = offset; index < offset + CHUNK_BYTES; index++) {
		u8 actual = READ_ONCE(destination[index]);

		if (actual != pango_expected_byte(index)) {
			dev_err(&pdev->dev,
				"chunk %zu byte %zu: expected %#02x, got %#02x\n",
				offset / CHUNK_BYTES, index,
				pango_expected_byte(index), actual);
			break;
		}
	}

	return -ETIMEDOUT;
}

static int pango_pattern_probe(struct pci_dev *pdev,
			       const struct pci_device_id *id)
{
	void __iomem *bar0;
	void __iomem *bar1;
	u8 *destination;
	dma_addr_t dma_address;
	size_t offset;
	int ret;

	ret = pcim_enable_device(pdev);
	if (ret)
		return ret;

	if (!(pci_resource_flags(pdev, PANGO_BAR0) & IORESOURCE_MEM) ||
	    !(pci_resource_flags(pdev, PANGO_BAR1) & IORESOURCE_MEM) ||
	    pci_resource_len(pdev, PANGO_BAR0) < 0x204 ||
	    pci_resource_len(pdev, PANGO_BAR1) < 0x184) {
		dev_err(&pdev->dev, "BAR0 or BAR1 is unavailable or too small\n");
		return -ENODEV;
	}

	ret = pcim_iomap_regions(pdev, BIT(PANGO_BAR0) | BIT(PANGO_BAR1),
				 "pango_pcie_fpga_pattern_test");
	if (ret)
		return ret;

	bar0 = pcim_iomap_table(pdev)[PANGO_BAR0];
	bar1 = pcim_iomap_table(pdev)[PANGO_BAR1];
	if (!bar0 || !bar1)
		return -ENODEV;

	ret = dma_set_mask_and_coherent(&pdev->dev, DMA_BIT_MASK(32));
	if (ret) {
		dev_err(&pdev->dev, "32-bit coherent DMA is unavailable: %d\n", ret);
		return ret;
	}

	destination = dma_alloc_coherent(&pdev->dev, FRAME_BYTES,
					 &dma_address, GFP_KERNEL);
	if (!destination) {
		dev_err(&pdev->dev, "failed to allocate coherent DMA buffer\n");
		return -ENOMEM;
	}

	memset(destination, 0xa5, FRAME_BYTES);
	dma_wmb();
	pci_set_master(pdev);
	dev_info(&pdev->dev, "FPGA-generated frame buffer at %pad\n",
		 &dma_address);

	writel(PATTERN_MAGIC, bar1 + PATTERN_COMMAND);
	readl(bar0 + 0x200);
	msleep(1);

	for (offset = 0; offset < FRAME_BYTES; offset += CHUNK_BYTES) {
		writel(DMA_COMMAND_WRITE, bar1 + DMA_COMMAND);
		readl(bar0 + 0x200);
		writel(lower_32_bits(dma_address + offset), bar1 + DMA_ADDR_LOW);
		readl(bar0 + 0x200);

		ret = pango_wait_for_chunk(pdev, destination, offset);
		if (ret)
			goto out;
	}

	dma_rmb();
	for (offset = 0; offset < FRAME_BYTES; offset++) {
		if (destination[offset] != pango_expected_byte(offset)) {
			dev_err(&pdev->dev, "final frame mismatch at byte %zu\n",
				offset);
			ret = -EIO;
			goto out;
		}
	}

	dev_info(&pdev->dev, "FPGA-generated 64x64 frame DMA passed\n");
	ret = 0;

out:
	pci_clear_master(pdev);
	msleep(100);
	dma_free_coherent(&pdev->dev, FRAME_BYTES, destination, dma_address);
	return ret;
}

static const struct pci_device_id pango_pattern_ids[] = {
	{ PCI_DEVICE(PANGO_VENDOR_ID, PANGO_DEVICE_ID) },
	{ }
};
MODULE_DEVICE_TABLE(pci, pango_pattern_ids);

static struct pci_driver pango_pattern_driver = {
	.name = "pango_pcie_fpga_pattern_test",
	.id_table = pango_pattern_ids,
	.probe = pango_pattern_probe,
};
module_pci_driver(pango_pattern_driver);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Pango PCIe FPGA-generated test frame DMA");
