#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/fs.h>
#include <linux/jiffies.h>
#include <linux/kref.h>
#include <linux/ktime.h>
#include <linux/miscdevice.h>
#include <linux/mm.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/pci.h>
#include <linux/slab.h>
#include <linux/uaccess.h>
#include <linux/vmalloc.h>

#include "pcie_multivideo_protocol.h"

#define VIDEO_CAPABILITIES 0x150
#define VIDEO_CAPABILITY_MAGIC 0x4d565331
#define VIDEO_ADDR_LOW 0x130
#define VIDEO_ADDR_HIGH 0x140
#define VIDEO_START 0x150
#define VIDEO_SOURCE 0x160
#define VIDEO_BUSY BIT(0)
#define VIDEO_DONE BIT(1)
#define VIDEO_ERROR BIT(2)
#define VIDEO_TIMEOUT_MS 20000

static bool profile;
module_param(profile, bool, 0644);
MODULE_PARM_DESC(profile, "Log per-stage video capture and record delivery timings");

static bool cached_dma;
module_param(cached_dma, bool, 0444);
MODULE_PARM_DESC(cached_dma, "Use a cached streaming DMA buffer, falling back to coherent allocation");

struct pango_video_context {
	struct pci_dev *pdev;
	struct miscdevice device;
	struct kref references;
	struct mutex lock;
	void __iomem *bar0;
	void __iomem *bar1;
	void *pixels;
	dma_addr_t dma_address;
	bool cached_pixels;
	u64 sequence[3];
	bool opened;
	bool removed;
	bool needs_reset;
};

struct pango_video_reader {
	struct pango_video_context *context;
	struct mutex lock;
	u8 *record;
	size_t record_bytes;
	size_t position;
	u32 source;
	u32 cycle_sources;
	bool profile_record;
	u64 user_copy_ns;
};

static u32 pango_video_checksum(const u8 *pixels, size_t bytes)
{
	u32 checksum = 2166136261U;
	size_t index;

	for (index = 0; index < bytes; index++)
		checksum = (checksum ^ pixels[index]) * 16777619U;
	return checksum;
}

static int pango_video_alloc_pixels(struct pango_video_context *context)
{
	struct device *device = &context->pdev->dev;

	if (cached_dma) {
		context->pixels = alloc_pages_exact(PANGO_VIDEO_HDMI_BYTES,
					GFP_KERNEL | GFP_DMA32 | __GFP_NOWARN);
		if (context->pixels) {
			context->dma_address = dma_map_single(device, context->pixels,
						PANGO_VIDEO_HDMI_BYTES, DMA_BIDIRECTIONAL);
			if (!dma_mapping_error(device, context->dma_address)) {
				context->cached_pixels = true;
				dma_sync_single_for_cpu(device, context->dma_address,
						PANGO_VIDEO_HDMI_BYTES, DMA_BIDIRECTIONAL);
				return 0;
			}
			free_pages_exact(context->pixels, PANGO_VIDEO_HDMI_BYTES);
		}
		dev_warn(device, "cached DMA allocation or mapping failed; using coherent buffer\n");
	}
	context->pixels = dma_alloc_coherent(device, PANGO_VIDEO_HDMI_BYTES,
					     &context->dma_address, GFP_KERNEL);
	return context->pixels ? 0 : -ENOMEM;
}

static void pango_video_context_release(struct kref *references)
{
	struct pango_video_context *context =
		container_of(references, struct pango_video_context, references);

	if (context->pixels) {
		if (context->cached_pixels) {
			dma_unmap_single(&context->pdev->dev, context->dma_address,
					 PANGO_VIDEO_HDMI_BYTES, DMA_BIDIRECTIONAL);
			free_pages_exact(context->pixels, PANGO_VIDEO_HDMI_BYTES);
		} else {
			dma_free_coherent(&context->pdev->dev, PANGO_VIDEO_HDMI_BYTES,
					  context->pixels, context->dma_address);
		}
	}
	kfree(context->device.name);
	pci_dev_put(context->pdev);
	kfree(context);
}

static int pango_video_wait(struct pango_video_context *context)
{
	unsigned long deadline = jiffies + msecs_to_jiffies(VIDEO_TIMEOUT_MS);
	bool started = false;
	u32 status;

	do {
		status = readl(context->bar0);
		if (status == U32_MAX)
			return -ENODEV;
		if (status & VIDEO_ERROR)
			return -EIO;
		if ((status & VIDEO_BUSY) || !(status & VIDEO_DONE))
			started = true;
		if (started && (status & VIDEO_DONE) && !(status & VIDEO_BUSY))
			return 0;
		usleep_range(1000, 2000);
	} while (time_before(jiffies, deadline));
	dev_err(&context->pdev->dev, "video DMA timeout: status=%#x\n", status);
	return -ETIMEDOUT;
}

static int pango_video_capture(struct pango_video_reader *reader)
{
	struct pango_video_context *context = reader->context;
	struct pango_video_header *header = (void *)reader->record;
	u32 source = reader->source;
	u32 width = source == PANGO_VIDEO_HDMI ? 1920 : 640;
	u32 height = source == PANGO_VIDEO_HDMI ? 1080 : 480;
	u32 bytes = width * height * 2;
	u32 offset = 0x100 + source * 16;
	u64 timestamp;
	u64 started;
	u64 requested = 0;
	u64 prepared = 0;
	u64 filled = 0;
	u64 device_synced = 0;
	u64 cpu_synced = 0;
	u64 copied = 0;
	u64 checksummed = 0;
	bool collect_timing = READ_ONCE(profile);
	int ret;

	if (collect_timing)
		requested = ktime_get_ns();
	ret = mutex_lock_interruptible(&context->lock);
	if (ret)
		return ret;
	if (context->removed) {
		ret = -ENODEV;
		goto unlock;
	}
	if (context->needs_reset) {
		ret = -EIO;
		goto unlock;
	}
	if (source == PANGO_VIDEO_ETHERNET &&
	    (!(readl(context->bar0 + VIDEO_CAPABILITIES + 4) & BIT(2)) ||
	     !(readl(context->bar0 + 0x160) & BIT(0)))) {
		ret = -EAGAIN;
		goto unlock;
	}
	if (readl(context->bar0 + offset) != source ||
	    readl(context->bar0 + offset + 4) != ((height << 16) | width) ||
	    readl(context->bar0 + offset + 8) != PANGO_VIDEO_RGB565LE ||
	    readl(context->bar0 + offset + 12) != bytes) {
		ret = -EPROTO;
		goto unlock;
	}
	if (readl(context->bar0) & VIDEO_BUSY) {
		context->needs_reset = true;
		pci_clear_master(context->pdev);
		msleep(100);
		dev_err(&context->pdev->dev, "FPGA already busy; reset FPGA before retry\n");
		ret = -EBUSY;
		goto unlock;
	}

	if (collect_timing)
		prepared = ktime_get_ns();
	memset(context->pixels, 0xa5, bytes);
	if (collect_timing)
		filled = ktime_get_ns();
	if (context->cached_pixels)
		dma_sync_single_for_device(&context->pdev->dev, context->dma_address,
					  PANGO_VIDEO_HDMI_BYTES, DMA_BIDIRECTIONAL);
	if (collect_timing)
		device_synced = ktime_get_ns();
	pci_set_master(context->pdev);
	dma_wmb();
	writel(source, context->bar1 + VIDEO_SOURCE);
	readl(context->bar0);
	writel(lower_32_bits(context->dma_address), context->bar1 + VIDEO_ADDR_LOW);
	readl(context->bar0);
	writel(upper_32_bits(context->dma_address), context->bar1 + VIDEO_ADDR_HIGH);
	readl(context->bar0);
	started = ktime_get_ns();
	writel(1, context->bar1 + VIDEO_START);
	readl(context->bar0);
	ret = pango_video_wait(context);
	if (ret) {
		context->needs_reset = true;
		dev_err(&context->pdev->dev,
			"source=%u DMA failed: %d; reset FPGA and re-enumerate before retry\n",
			source, ret);
		pci_clear_master(context->pdev);
		msleep(100);
		goto unlock;
	}
	dma_rmb();
	timestamp = ktime_get_ns();
	if (context->cached_pixels)
		dma_sync_single_for_cpu(&context->pdev->dev, context->dma_address,
				       PANGO_VIDEO_HDMI_BYTES, DMA_BIDIRECTIONAL);
	if (collect_timing)
		cpu_synced = ktime_get_ns();
	memcpy(reader->record + sizeof(*header), context->pixels, bytes);
	if (collect_timing)
		copied = ktime_get_ns();
	memset(header, 0, sizeof(*header));
	header->magic = cpu_to_le32(PANGO_VIDEO_MAGIC);
	header->version = cpu_to_le16(PANGO_VIDEO_VERSION);
	header->header_bytes = cpu_to_le16(sizeof(*header));
	header->source = cpu_to_le32(source);
	header->format = cpu_to_le32(PANGO_VIDEO_RGB565LE);
	header->width = cpu_to_le32(width);
	header->height = cpu_to_le32(height);
	header->pixel_bytes = cpu_to_le32(bytes);
	header->checksum = cpu_to_le32(pango_video_checksum(
		reader->record + sizeof(*header), bytes));
	if (collect_timing)
		checksummed = ktime_get_ns();
	header->sequence = cpu_to_le64(++context->sequence[source]);
	header->timestamp_ns = cpu_to_le64(timestamp);
	if (source == PANGO_VIDEO_HDMI)
		header->hdmi_timestamp_ns = cpu_to_le64(timestamp);
	else if (source == PANGO_VIDEO_CAMERA)
		header->camera_timestamp_ns = cpu_to_le64(timestamp);
	reader->record_bytes = sizeof(*header) + bytes;
	reader->profile_record = collect_timing;
	reader->user_copy_ns = 0;
	pci_clear_master(context->pdev);
	if (collect_timing) {
		u64 finished = ktime_get_ns();

		dev_info(&context->pdev->dev,
			 "source=%u bytes=%u seq=%llu capture_us=%llu "
			 "prepare_us=%llu fill_us=%llu sync_device_us=%llu submit_us=%llu "
			 "sync_cpu_us=%llu copy_us=%llu checksum_us=%llu "
			 "finish_us=%llu record_us=%llu dma_buffer=%s\n",
			 source, bytes,
			 (unsigned long long)context->sequence[source],
			 (unsigned long long)((timestamp - started) / 1000),
			 (unsigned long long)((prepared - requested) / 1000),
			 (unsigned long long)((filled - prepared) / 1000),
			 (unsigned long long)((device_synced - filled) / 1000),
			 (unsigned long long)((started - device_synced) / 1000),
			 (unsigned long long)((cpu_synced - timestamp) / 1000),
			 (unsigned long long)((copied - cpu_synced) / 1000),
			 (unsigned long long)((checksummed - copied) / 1000),
			 (unsigned long long)((finished - checksummed) / 1000),
			 (unsigned long long)((finished - requested) / 1000),
			 context->cached_pixels ? "cached" : "coherent");
	}
unlock:
	mutex_unlock(&context->lock);
	return ret;
}

static int pango_video_open(struct inode *inode, struct file *file)
{
	struct pango_video_context *context =
		container_of(file->private_data, struct pango_video_context, device);
	struct pango_video_reader *reader;
	int ret;

	reader = kzalloc(sizeof(*reader), GFP_KERNEL);
	if (!reader)
		return -ENOMEM;
	reader->record = vmalloc(PANGO_VIDEO_HDMI_BYTES + PANGO_VIDEO_HEADER_BYTES);
	if (!reader->record) {
		kfree(reader);
		return -ENOMEM;
	}
	ret = mutex_lock_interruptible(&context->lock);
	if (ret)
		goto free_reader;
	if (context->removed || context->opened) {
		ret = context->removed ? -ENODEV : -EBUSY;
		mutex_unlock(&context->lock);
		goto free_reader;
	}
	context->opened = true;
	kref_get(&context->references);
	mutex_unlock(&context->lock);
	reader->context = context;
	reader->cycle_sources = 2;
	mutex_init(&reader->lock);
	file->private_data = reader;
	return nonseekable_open(inode, file);
free_reader:
	vfree(reader->record);
	kfree(reader);
	return ret;
}

static ssize_t pango_video_read(struct file *file, char __user *buffer,
			       size_t count, loff_t *position)
{
	struct pango_video_reader *reader = file->private_data;
	size_t available;
	u64 copy_started = 0;
	ssize_t ret;

	if (!count)
		return 0;
	ret = mutex_lock_interruptible(&reader->lock);
	if (ret)
		return ret;
	if (!reader->record_bytes) {
		ret = pango_video_capture(reader);
		if (ret)
			goto unlock;
	}
	available = min(count, reader->record_bytes - reader->position);
	if (reader->profile_record)
		copy_started = ktime_get_ns();
	if (copy_to_user(buffer, reader->record + reader->position, available)) {
		ret = -EFAULT;
		goto unlock;
	}
	if (reader->profile_record)
		reader->user_copy_ns += ktime_get_ns() - copy_started;
	reader->position += available;
	if (reader->position == reader->record_bytes) {
		if (reader->profile_record) {
			struct pango_video_header *header = (void *)reader->record;
			u64 sequence = le64_to_cpu(header->sequence);

			if (sequence <= 3 || sequence % 10 == 0)
				dev_info(&reader->context->pdev->dev,
					 "source=%u seq=%llu user_copy_us=%llu record_delivery=complete\n",
					 reader->source, (unsigned long long)sequence,
					 (unsigned long long)(reader->user_copy_ns / 1000));
		}
		reader->position = 0;
		reader->record_bytes = 0;
		if (reader->cycle_sources)
			reader->source = (reader->source + 1) % reader->cycle_sources;
	}
	ret = available;
unlock:
	mutex_unlock(&reader->lock);
	return ret;
}

static long pango_video_ioctl(struct file *file, unsigned int command, unsigned long argument)
{
	struct pango_video_reader *reader = file->private_data;
	u32 mode;
	int ret;

	if (command != PANGO_VIDEO_SET_MODE)
		return -ENOTTY;
	if (copy_from_user(&mode, (void __user *)argument, sizeof(mode)))
		return -EFAULT;
	if (mode > PANGO_VIDEO_ALL)
		return -EINVAL;
	ret = mutex_lock_interruptible(&reader->lock);
	if (ret)
		return ret;
	if (reader->record_bytes) {
		ret = -EBUSY;
	} else {
		reader->cycle_sources = mode == PANGO_VIDEO_ALL ? 3 : mode == PANGO_VIDEO_BOTH ? 2 : 0;
		reader->source = mode == PANGO_VIDEO_MODE_ETHERNET ? PANGO_VIDEO_ETHERNET :
				mode >= PANGO_VIDEO_BOTH ? 0 : mode;
	}
	mutex_unlock(&reader->lock);
	return ret;
}

static int pango_video_release(struct inode *inode, struct file *file)
{
	struct pango_video_reader *reader = file->private_data;

	mutex_lock(&reader->context->lock);
	reader->context->opened = false;
	mutex_unlock(&reader->context->lock);
	vfree(reader->record);
	kref_put(&reader->context->references, pango_video_context_release);
	kfree(reader);
	return 0;
}

static const struct file_operations pango_video_operations = {
	.owner = THIS_MODULE,
	.open = pango_video_open,
	.read = pango_video_read,
	.unlocked_ioctl = pango_video_ioctl,
	.release = pango_video_release,
	.llseek = no_llseek,
};

static int pango_video_probe(struct pci_dev *pdev, const struct pci_device_id *id)
{
	struct pango_video_context *context;
	void __iomem *bar0;
	int ret;

	BUILD_BUG_ON(sizeof(struct pango_video_header) != PANGO_VIDEO_HEADER_BYTES);
	ret = pcim_enable_device(pdev);
	if (ret)
		return ret;
	if (!(pci_resource_flags(pdev, 0) & IORESOURCE_MEM) ||
	    !(pci_resource_flags(pdev, 1) & IORESOURCE_MEM) ||
	    pci_resource_len(pdev, 0) < 0x160 || pci_resource_len(pdev, 1) < 0x164)
		return -ENODEV;
	ret = pcim_iomap_regions(pdev, BIT(0) | BIT(1), "pango_pcie_multivideo");
	if (ret)
		return ret;
	bar0 = pcim_iomap_table(pdev)[0];
	if (readl(bar0 + VIDEO_CAPABILITIES) != VIDEO_CAPABILITY_MAGIC ||
	    (readl(bar0 + VIDEO_CAPABILITIES + 4) & 3) != 3 ||
	    readl(bar0 + VIDEO_CAPABILITIES + 8) != VIDEO_SOURCE ||
	    readl(bar0 + VIDEO_CAPABILITIES + 12) != 1 ||
	    readl(bar0 + 4) != PANGO_VIDEO_HDMI_BYTES ||
	    readl(bar0 + 8) != 0x52473536) {
		dev_err(&pdev->dev, "program the RGB565 HDMI + OV5640 DMA bitstream first\n");
		return -EPROTO;
	}
	ret = dma_set_mask_and_coherent(&pdev->dev, DMA_BIT_MASK(32));
	if (ret)
		return ret;
	context = kzalloc(sizeof(*context), GFP_KERNEL);
	if (!context)
		return -ENOMEM;
	context->pdev = pci_dev_get(pdev);
	context->bar0 = bar0;
	context->bar1 = pcim_iomap_table(pdev)[1];
	kref_init(&context->references);
	mutex_init(&context->lock);
	ret = pango_video_alloc_pixels(context);
	if (ret)
		goto release;
	context->device.name = kasprintf(GFP_KERNEL, "pango_pcie_multivideo_%s", pci_name(pdev));
	if (!context->device.name) {
		ret = -ENOMEM;
		goto release;
	}
	context->device.minor = MISC_DYNAMIC_MINOR;
	context->device.fops = &pango_video_operations;
	context->device.parent = &pdev->dev;
	context->device.mode = 0444;
	ret = misc_register(&context->device);
	if (ret)
		goto release;
	pci_set_drvdata(pdev, context);
	dev_info(&pdev->dev,
		 "dual-source video ready: /dev/%s; one %u-byte DMA buffer; dma_buffer=%s\n",
		 context->device.name, PANGO_VIDEO_HDMI_BYTES,
		 context->cached_pixels ? "cached" : "coherent");
	return 0;
release:
	kref_put(&context->references, pango_video_context_release);
	return ret;
}

static void pango_video_remove(struct pci_dev *pdev)
{
	struct pango_video_context *context = pci_get_drvdata(pdev);

	misc_deregister(&context->device);
	mutex_lock(&context->lock);
	context->removed = true;
	pci_clear_master(pdev);
	mutex_unlock(&context->lock);
	kref_put(&context->references, pango_video_context_release);
}

static const struct pci_device_id pango_video_ids[] = {
	{ PCI_DEVICE(0x0755, 0x0755) },
	{ }
};
MODULE_DEVICE_TABLE(pci, pango_video_ids);

static struct pci_driver pango_video_driver = {
	.name = "pango_pcie_multivideo",
	.id_table = pango_video_ids,
	.probe = pango_video_probe,
	.remove = pango_video_remove,
};
module_pci_driver(pango_video_driver);
MODULE_LICENSE("GPL");
MODULE_VERSION("1.3");
MODULE_DESCRIPTION("HDMI, OV5640 and Ethernet source-tagged PCIe video capture");
