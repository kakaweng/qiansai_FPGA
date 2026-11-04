#include <linux/io.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/pci.h>

#define PANGO_VENDOR_ID 0x0755
#define PANGO_DEVICE_ID 0x0755
#define PANGO_TEST_BAR 0

struct pango_bar_test_word {
	resource_size_t offset;
	u32 value;
};

static const struct pango_bar_test_word pango_test_words[] = {
	{ 0x200, 0x13579bdf },
	{ 0x204, 0x2468ace0 },
	{ 0x210, 0xa55a3cc3 },
};

static int pango_bar_probe(struct pci_dev *pdev,
			   const struct pci_device_id *id)
{
	void __iomem *bar0;
	u32 actual;
	int ret;
	size_t index;

	ret = pcim_enable_device(pdev);
	if (ret)
		return ret;

	if (!(pci_resource_flags(pdev, PANGO_TEST_BAR) & IORESOURCE_MEM) ||
	    pci_resource_len(pdev, PANGO_TEST_BAR) < 0x214) {
		dev_err(&pdev->dev, "BAR0 is not memory or is too small\n");
		return -ENODEV;
	}

	ret = pcim_iomap_regions(pdev, BIT(PANGO_TEST_BAR), "pango_pcie_bar_test");
	if (ret)
		return ret;

	bar0 = pcim_iomap_table(pdev)[PANGO_TEST_BAR];
	if (!bar0)
		return -ENODEV;

	dev_info(&pdev->dev, "BAR0 size %#llx; starting MMIO test\n",
		 (unsigned long long)pci_resource_len(pdev, PANGO_TEST_BAR));

	for (index = 0; index < ARRAY_SIZE(pango_test_words); index++)
		writel(pango_test_words[index].value,
		       bar0 + pango_test_words[index].offset);

	for (index = 0; index < ARRAY_SIZE(pango_test_words); index++) {
		actual = readl(bar0 + pango_test_words[index].offset);
		if (actual != pango_test_words[index].value) {
			dev_err(&pdev->dev,
				"BAR0 offset %#llx: expected %#08x, got %#08x\n",
				(unsigned long long)pango_test_words[index].offset,
				pango_test_words[index].value, actual);
			return -EIO;
		}
	}

	dev_info(&pdev->dev, "BAR0 MMIO round-trip passed (3 words)\n");
	return 0;
}

static const struct pci_device_id pango_bar_ids[] = {
	{ PCI_DEVICE(PANGO_VENDOR_ID, PANGO_DEVICE_ID) },
	{ }
};
MODULE_DEVICE_TABLE(pci, pango_bar_ids);

static struct pci_driver pango_bar_driver = {
	.name = "pango_pcie_bar_test",
	.id_table = pango_bar_ids,
	.probe = pango_bar_probe,
};
module_pci_driver(pango_bar_driver);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Pango PCIe example BAR0 MMIO round-trip test");
