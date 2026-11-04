#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/io.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/pci.h>
#include <linux/random.h>

#define PANGO_VENDOR_ID 0x0755
#define PANGO_DEVICE_ID 0x0755
#define PANGO_BAR0 0
#define PANGO_BAR1 1

#define TEST_BYTES 64
#define TEST_BUFFER_BYTES (2 * TEST_BYTES)
#define TEST_TIMEOUT_MS 2000
#define DMA_ADDR_LOW 0x110
#define DMA_COMMAND 0x100
#define DMA_COMMAND_32_READ 0x0000000f
#define DMA_COMMAND_32_WRITE 0x0100000f

static bool pango_data_matches(const u8 *source, const u8 *destination)
{
	size_t index;

	for (index = 0; index < TEST_BYTES; index++) {
		if (READ_ONCE(destination[index]) != source[index])
			return false;
	}

	return true;
}

static void pango_issue_dma(void __iomem *bar0, void __iomem *bar1,
			    dma_addr_t address, u32 command)
{
	writel(command, bar1 + DMA_COMMAND);
	readl(bar0 + 0x200);
	writel(lower_32_bits(address), bar1 + DMA_ADDR_LOW);
	readl(bar0 + 0x200);
}

static int pango_small_dma_probe(struct pci_dev *pdev,
				 const struct pci_device_id *id)
{
	void __iomem *bar0;
	void __iomem *bar1;
	u8 *buffer;
	u8 *destination;
	dma_addr_t dma_address;
	unsigned long timeout;
	size_t index;
	int ret;

	ret = pcim_enable_device(pdev);
	if (ret)
		return ret;

	if (!(pci_resource_flags(pdev, PANGO_BAR0) & IORESOURCE_MEM) ||
	    !(pci_resource_flags(pdev, PANGO_BAR1) & IORESOURCE_MEM) ||
	    pci_resource_len(pdev, PANGO_BAR0) < 0x204 ||
	    pci_resource_len(pdev, PANGO_BAR1) < 0x114) {
		dev_err(&pdev->dev, "BAR0 or BAR1 is unavailable or too small\n");
		return -ENODEV;
	}

	ret = pcim_iomap_regions(pdev, BIT(PANGO_BAR0) | BIT(PANGO_BAR1),
				 "pango_pcie_small_dma_test");
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

	buffer = dma_alloc_coherent(&pdev->dev, TEST_BUFFER_BYTES,
				    &dma_address, GFP_KERNEL);
	if (!buffer) {
		dev_err(&pdev->dev, "failed to allocate coherent DMA buffer\n");
		return -ENOMEM;
	}

	destination = buffer + TEST_BYTES;
	get_random_bytes(buffer, TEST_BYTES);
	memset(destination, 0xa5, TEST_BYTES);
	dma_wmb();

	pci_set_master(pdev);
	dev_info(&pdev->dev, "64-byte DMA test buffer at %pad\n", &dma_address);

	pango_issue_dma(bar0, bar1, dma_address, DMA_COMMAND_32_READ);
	msleep(100);
	pango_issue_dma(bar0, bar1, dma_address + TEST_BYTES,
			DMA_COMMAND_32_WRITE);

	timeout = jiffies + msecs_to_jiffies(TEST_TIMEOUT_MS);
	do {
		dma_rmb();
		if (pango_data_matches(buffer, destination)) {
			dev_info(&pdev->dev,
				 "64-byte FPGA DMA read/write round-trip passed\n");
			ret = 0;
			goto out;
		}
		msleep(10);
	} while (time_before(jiffies, timeout));

	for (index = 0; index < TEST_BYTES; index++) {
		if (READ_ONCE(destination[index]) != buffer[index]) {
			dev_err(&pdev->dev,
				"DMA timeout: byte %zu expected %#02x, got %#02x\n",
				index, buffer[index], destination[index]);
			break;
		}
	}
	ret = -ETIMEDOUT;

out:
	pci_clear_master(pdev);
	msleep(100);
	dma_free_coherent(&pdev->dev, TEST_BUFFER_BYTES, buffer, dma_address);
	return ret;
}

static const struct pci_device_id pango_small_dma_ids[] = {
	{ PCI_DEVICE(PANGO_VENDOR_ID, PANGO_DEVICE_ID) },
	{ }
};
MODULE_DEVICE_TABLE(pci, pango_small_dma_ids);

static struct pci_driver pango_small_dma_driver = {
	.name = "pango_pcie_small_dma_test",
	.id_table = pango_small_dma_ids,
	.probe = pango_small_dma_probe,
};
module_pci_driver(pango_small_dma_driver);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Pango PCIe example 64-byte DMA round-trip test");
