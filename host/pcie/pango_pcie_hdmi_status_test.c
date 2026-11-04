#include <linux/io.h>
#include <linux/module.h>
#include <linux/pci.h>

#define PANGO_VENDOR_ID 0x0755
#define PANGO_DEVICE_ID 0x0755
#define PANGO_BAR_STATUS 0
#define PANGO_BAR_CONTROL 1
#define PANGO_STATUS_BYTES 0x10
#define PANGO_CONTROL_BYTES 0x154
#define PANGO_FRAME_BYTES (1920 * 1080)
#define PANGO_FRAME_SIGNATURE 0x46524d45
#define PANGO_RGB565_BYTES (PANGO_FRAME_BYTES * 2)
#define PANGO_RGB565_SIGNATURE 0x52473536

static int pango_hdmi_status_probe(struct pci_dev *pdev,
				   const struct pci_device_id *id)
{
	void __iomem *bar0;
	u32 status;
	u32 frame_bytes;
	u32 signature;
	u32 reserved;
	int ret;

	ret = pcim_enable_device(pdev);
	if (ret)
		return ret;

	if (!(pci_resource_flags(pdev, PANGO_BAR_STATUS) & IORESOURCE_MEM) ||
	    !(pci_resource_flags(pdev, PANGO_BAR_CONTROL) & IORESOURCE_MEM) ||
	    pci_resource_len(pdev, PANGO_BAR_STATUS) < PANGO_STATUS_BYTES ||
	    pci_resource_len(pdev, PANGO_BAR_CONTROL) < PANGO_CONTROL_BYTES) {
		dev_err(&pdev->dev, "BAR0 or BAR1 is unavailable or too small\n");
		return -ENODEV;
	}

	ret = pcim_iomap_regions(pdev, BIT(PANGO_BAR_STATUS),
				 "pango_pcie_hdmi_status_test");
	if (ret)
		return ret;

	bar0 = pcim_iomap_table(pdev)[PANGO_BAR_STATUS];
	if (!bar0)
		return -ENODEV;

	status = readl(bar0);
	frame_bytes = readl(bar0 + 4);
	signature = readl(bar0 + 8);
	reserved = readl(bar0 + 12);
	dev_info(&pdev->dev,
		 "HDMI bridge BAR0: status=%#08x bytes=%u signature=%#08x reserved=%#08x\n",
		 status, frame_bytes, signature, reserved);

	if (!((signature == PANGO_FRAME_SIGNATURE && frame_bytes == PANGO_FRAME_BYTES) ||
	      (signature == PANGO_RGB565_SIGNATURE && frame_bytes == PANGO_RGB565_BYTES))) {
		dev_err(&pdev->dev, "HDMI frame bridge signature mismatch\n");
		return -ENODEV;
	}

	dev_info(&pdev->dev, "HDMI frame bridge status read passed\n");
	return 0;
}

static const struct pci_device_id pango_hdmi_status_ids[] = {
	{ PCI_DEVICE(PANGO_VENDOR_ID, PANGO_DEVICE_ID) },
	{ }
};
MODULE_DEVICE_TABLE(pci, pango_hdmi_status_ids);

static struct pci_driver pango_hdmi_status_driver = {
	.name = "pango_pcie_hdmi_status_test",
	.id_table = pango_hdmi_status_ids,
	.probe = pango_hdmi_status_probe,
};
module_pci_driver(pango_hdmi_status_driver);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Pango HDMI PCIe bridge BAR0 status test");
