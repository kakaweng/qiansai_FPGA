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
#define FRAME_COUNT 2
#define CHUNK_BYTES 256
#define DMA_BUFFER_BYTES (2 * FRAME_BYTES)
#define DMA_TIMEOUT_MS 2000
#define DMA_ADDR_LOW 0x110
#define DMA_COMMAND 0x100
#define DMA_COMMAND_READ ((CHUNK_BYTES / 4) - 1)
#define DMA_COMMAND_WRITE (0x01000000 | DMA_COMMAND_READ)

static void pango_fill_frame(u8 *source, unsigned int frame_number)
{
	size_t index;

	for (index = 0; index < FRAME_BYTES; index++) {
		size_t pixel_x = index % FRAME_WIDTH;
		size_t pixel_y = index / FRAME_WIDTH;

		source[index] = (u8)(pixel_x * 3 + pixel_y * 5 +
				     frame_number * 71);
	}
}

static bool pango_chunk_matches(const u8 *source, const u8 *destination,
				size_t offset)
{
	size_t index;

	for (index = 0; index < CHUNK_BYTES; index++) {
		if (READ_ONCE(destination[offset + index]) !=
		    source[offset + index])
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

static int pango_wait_for_chunk(struct pci_dev *pdev, const u8 *source,
				const u8 *destination, size_t offset,
				unsigned int frame_number)
{
	unsigned long timeout = jiffies + msecs_to_jiffies(DMA_TIMEOUT_MS);
	size_t index;

	do {
		dma_rmb();
		if (pango_chunk_matches(source, destination, offset))
			return 0;
		msleep(10);
	} while (time_before(jiffies, timeout));

	for (index = offset; index < offset + CHUNK_BYTES; index++) {
		if (READ_ONCE(destination[index]) != source[index]) {
			dev_err(&pdev->dev,
				"frame %u chunk %zu byte %zu: expected %#02x, got %#02x\n",
				frame_number, offset / CHUNK_BYTES, index,
				source[index], destination[index]);
			break;
		}
	}

	return -ETIMEDOUT;
}

static int pango_frame_dma_probe(struct pci_dev *pdev,
				 const struct pci_device_id *id)
{
	void __iomem *bar0;
	void __iomem *bar1;
	u8 *buffer;
	u8 *destination;
	dma_addr_t dma_address;
	unsigned int frame_number;
	size_t offset;
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
				 "pango_pcie_frame_dma_test");
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

	buffer = dma_alloc_coherent(&pdev->dev, DMA_BUFFER_BYTES,
				    &dma_address, GFP_KERNEL);
	if (!buffer) {
		dev_err(&pdev->dev, "failed to allocate coherent DMA buffer\n");
		return -ENOMEM;
	}

	destination = buffer + FRAME_BYTES;
	pci_set_master(pdev);
	dev_info(&pdev->dev,
		 "two 64x64 frames, 256-byte chunks, DMA buffer at %pad\n",
		 &dma_address);

	for (frame_number = 0; frame_number < FRAME_COUNT; frame_number++) {
		pango_fill_frame(buffer, frame_number);
		memset(destination, 0xa5, FRAME_BYTES);
		dma_wmb();

		for (offset = 0; offset < FRAME_BYTES; offset += CHUNK_BYTES) {
			pango_issue_dma(bar0, bar1, dma_address + offset,
					DMA_COMMAND_READ);
			msleep(100);
			pango_issue_dma(bar0, bar1,
						dma_address + FRAME_BYTES + offset,
						DMA_COMMAND_WRITE);
			ret = pango_wait_for_chunk(pdev, buffer, destination,
						   offset, frame_number);
			if (ret)
				goto out;
		}

		dma_rmb();
		if (memcmp(buffer, destination, FRAME_BYTES)) {
			dev_err(&pdev->dev, "frame %u final comparison failed\n",
				frame_number);
			ret = -EIO;
			goto out;
		}
		dev_info(&pdev->dev, "frame %u complete: %d bytes\n",
			 frame_number, FRAME_BYTES);
	}

	dev_info(&pdev->dev, "two-frame chunked DMA round-trip passed\n");
	ret = 0;

out:
	pci_clear_master(pdev);
	msleep(100);
	dma_free_coherent(&pdev->dev, DMA_BUFFER_BYTES, buffer, dma_address);
	return ret;
}

static const struct pci_device_id pango_frame_dma_ids[] = {
	{ PCI_DEVICE(PANGO_VENDOR_ID, PANGO_DEVICE_ID) },
	{ }
};
MODULE_DEVICE_TABLE(pci, pango_frame_dma_ids);

static struct pci_driver pango_frame_dma_driver = {
	.name = "pango_pcie_frame_dma_test",
	.id_table = pango_frame_dma_ids,
	.probe = pango_frame_dma_probe,
};
module_pci_driver(pango_frame_dma_driver);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Pango PCIe example two-frame chunked DMA test");
