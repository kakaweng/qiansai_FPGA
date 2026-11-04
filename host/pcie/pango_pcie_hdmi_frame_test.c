#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/fs.h>
#include <linux/jiffies.h>
#include <linux/kref.h>
#include <linux/ktime.h>
#include <linux/kthread.h>
#include <linux/math64.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/pci.h>
#include <linux/poll.h>
#include <linux/slab.h>
#include <linux/spinlock.h>
#include <linux/string.h>
#include <linux/sysfs.h>
#include <linux/uaccess.h>
#include <linux/vmalloc.h>
#include <linux/wait.h>

#define PANGO_VENDOR_ID 0x0755
#define PANGO_DEVICE_ID 0x0755
#define PANGO_BAR_STATUS 0
#define PANGO_BAR_CONTROL 1

#define PANGO_STATUS_BYTES 0x10
#define PANGO_CONTROL_BYTES 0x154
#define PANGO_FRAME_WIDTH 1920
#define PANGO_FRAME_HEIGHT 1080
#define PANGO_FRAME_BYTES (PANGO_FRAME_WIDTH * PANGO_FRAME_HEIGHT)
#define PANGO_RGB565_BYTES (PANGO_FRAME_BYTES * 2)
#define PANGO_FRAME_FILL 0xa5
#define PANGO_FRAME_SIGNATURE 0x46524d45
#define PANGO_RGB565_SIGNATURE 0x52473536
#define PANGO_FRAME_TIMEOUT_MS 20000
#define PANGO_FRAME_DEBUG 0xc
#define PANGO_DEBUG_VERSION 0xd4
#define PANGO_PGM_HEADER "P5\n1920 1080\n255\n"

#define PANGO_FRAME_ADDR_LOW 0x130
#define PANGO_FRAME_ADDR_HIGH 0x140
#define PANGO_FRAME_START 0x150
#define PANGO_MMIO_FLUSH 0x200
#define PANGO_STREAM_SLOTS 4
#define PANGO_STREAM_MAGIC 0x50475246
#define PANGO_STREAM_VERSION 1

struct pango_stream_header {
	__le32 magic;
	__le16 version;
	__le16 header_bytes;
	__le32 width;
	__le32 height;
	__le32 pixel_bytes;
	__le32 checksum;
	__le64 sequence;
	__le64 timestamp_ns;
} __packed;

#define PANGO_STREAM_HEADER_BYTES sizeof(struct pango_stream_header)
#define PANGO_STATUS_DMA_MODE BIT(0)
#define PANGO_STATUS_FRAME_DONE BIT(1)
#define PANGO_STATUS_FRAME_ERROR BIT(2)

static bool profile;
module_param(profile, bool, 0644);
MODULE_PARM_DESC(profile, "Log per-stage frame timings for the first three frames and every tenth sequence");

struct pango_frame_timing {
	u64 requested_ns;
	u64 locked_ns;
	u64 checked_ns;
	u64 filled_ns;
	u64 submit_start_ns;
	u64 submitted_ns;
	u64 wait_start_ns;
	u64 waited_ns;
	u64 copied_ns;
	u64 changed_ns;
	u64 checksum_ns;
	u64 ready_ns;
	u64 finished_ns;
	u64 interval_ns;
};

struct pango_frame_snapshot {
	struct bin_attribute image;
	u8 *data;
};

enum pango_stream_slot_state {
	PANGO_STREAM_SLOT_FREE,
	PANGO_STREAM_SLOT_CAPTURING,
	PANGO_STREAM_SLOT_READY,
	PANGO_STREAM_SLOT_READING,
};

struct pango_stream_slot {
	void *frame;
	dma_addr_t dma_address;
	enum pango_stream_slot_state state;
	u64 sequence;
	u64 timestamp_ns;
	u32 checksum;
};

struct pango_frame_context {
	struct pci_dev *pdev;
	u32 frame_bytes;
	bool rgb565;
	struct miscdevice capture_device;
	struct miscdevice stream_device;
	struct kref references;
	struct mutex capture_lock;
	struct mutex stream_lock;
	spinlock_t stream_ring_lock;
	wait_queue_head_t stream_wait;
	struct task_struct *stream_thread;
	struct pango_stream_slot stream_slots[PANGO_STREAM_SLOTS];
	unsigned int stream_ready_count;
	unsigned int stream_write_index;
	unsigned int stream_read_index;
	int stream_error;
	bool stream_open;
	struct pango_frame_snapshot *initial_image;
	void __iomem *bar0;
	void __iomem *bar1;
	void *frame;
	u8 *validation_frame;
	dma_addr_t dma_address;
	u64 frame_sequence;
	u64 last_frame_timestamp_ns;
	bool removed;
	bool needs_reset;
};

struct pango_frame_reader {
	struct pango_frame_context *context;
	struct mutex read_lock;
	u8 *image;
};

struct pango_stream_reader {
	struct pango_frame_context *context;
	struct pango_stream_slot *slot;
	struct pango_stream_header header;
	size_t record_position;
};

static ssize_t pango_frame_image_read(struct file *file, struct kobject *kobj,
				     struct bin_attribute *attribute, char *buffer,
				     loff_t offset, size_t count)
{
	struct pango_frame_snapshot *snapshot =
		container_of(attribute, struct pango_frame_snapshot, image);

	if (offset < 0)
		return -EINVAL;
	if (offset >= attribute->size)
		return 0;

	count = min_t(size_t, count, attribute->size - offset);
	memcpy(buffer, snapshot->data + offset, count);
	return count;
}

static int pango_publish_frame(struct pango_frame_context *context, const void *frame,
			       struct pango_frame_snapshot **image_out)
{
	struct pci_dev *pdev = context->pdev;
	struct pango_frame_snapshot *snapshot;
	size_t header_bytes = context->rgb565 ? 0 : sizeof(PANGO_PGM_HEADER) - 1;
	int ret;

	snapshot = devm_kzalloc(&pdev->dev, sizeof(*snapshot), GFP_KERNEL);
	if (!snapshot)
		return -ENOMEM;

	snapshot->image.size = header_bytes + context->frame_bytes;
	snapshot->data = vmalloc(snapshot->image.size);
	if (!snapshot->data)
		return -ENOMEM;

	memcpy(snapshot->data, PANGO_PGM_HEADER, header_bytes);
	memcpy(snapshot->data + header_bytes, frame, context->frame_bytes);
	sysfs_bin_attr_init(&snapshot->image);
	snapshot->image.attr.name = context->rgb565 ? "hdmi_frame.rgb565" : "hdmi_frame.pgm";
	snapshot->image.attr.mode = 0444;
	snapshot->image.read = pango_frame_image_read;
	ret = sysfs_create_bin_file(&pdev->dev.kobj, &snapshot->image);
	if (ret) {
		vfree(snapshot->data);
		return ret;
	}

	*image_out = snapshot;
	dev_info(&pdev->dev,
		 "HDMI frame image ready: /sys/bus/pci/devices/%s/%s (%zu bytes)\n",
		 pci_name(pdev), snapshot->image.attr.name, snapshot->image.size);
	return 0;
}

static void pango_log_frame_debug(struct pci_dev *pdev, void __iomem *bar0,
				  u32 status)
{
	static const char * const pcie_states[] = {
		"IDLE", "WAIT_DDR", "SEND_DMA", "WAIT_DMA"
	};
	static const char * const ddr_states[] = {
		"IDLE", "WAIT_READY", "WAIT_DATA", "WAIT_FRAME"
	};
	u32 debug = readl(bar0 + PANGO_FRAME_DEBUG);
	u32 pcie_state = (debug >> 3) & 7;
	u32 ddr_state = (debug >> 6) & 3;
	u32 chunk = (status >> 16) & 0x7ff;

	if ((debug >> 24) != PANGO_DEBUG_VERSION) {
		dev_info(&pdev->dev,
			 "HDMI debug unavailable: raw=%#010x; update the FPGA bitstream\n",
			 debug);
		return;
	}

	dev_info(&pdev->dev,
		 "HDMI debug: raw=%#010x pcie=%s(%u) ddr=%s(%u) "
		 "calib=%u frame_ready=%u active=%u ready=%u cmd_seen=%u "
		 "done_seen=%u beats=%u/%u arvalid=%u arready=%u req=%u busy=%u chunk=%u\n",
		 debug, pcie_state < ARRAY_SIZE(pcie_states) ?
		 pcie_states[pcie_state] : "UNKNOWN", pcie_state,
		 ddr_states[ddr_state], ddr_state,
		 (debug >> 15) & 1, (debug >> 14) & 1,
		 (debug >> 8) & 1, (debug >> 9) & 1,
		 (debug >> 11) & 1, (debug >> 10) & 1,
		 (debug >> 16) & 0xff,
		 readl(bar0 + 8) == PANGO_RGB565_SIGNATURE || chunk == 1012 ? 64U : 128U,
		 (debug >> 13) & 1, (debug >> 12) & 1,
		 (debug >> 1) & 1, (debug >> 2) & 1, chunk);
}

static u32 pango_frame_checksum(const u8 *buffer, u32 bytes)
{
	u32 checksum = 2166136261U;
	size_t index;

	for (index = 0; index < bytes; index++)
		checksum = (checksum ^ buffer[index]) * 16777619U;

	return checksum;
}

static unsigned int pango_frame_changed_bytes(const u8 *buffer, u32 bytes)
{
	unsigned int changed = 0;
	size_t index;

	for (index = 0; index < bytes; index++)
		if (buffer[index] != PANGO_FRAME_FILL)
			changed++;

	return changed;
}

static int pango_wait_for_frame(struct pci_dev *pdev, void __iomem *bar0,
				 u32 submitted_status, u32 *status_out)
{
	unsigned long timeout = jiffies +
		msecs_to_jiffies(PANGO_FRAME_TIMEOUT_MS);
	bool dma_started = submitted_status & PANGO_STATUS_DMA_MODE;
	bool frame_done_cleared = !(submitted_status & PANGO_STATUS_FRAME_DONE);
	u32 status;

	do {
		status = readl(bar0);
		if (status & PANGO_STATUS_FRAME_ERROR) {
			dev_err(&pdev->dev, "HDMI frame bridge reported error: status=%#08x\n",
				status);
			pango_log_frame_debug(pdev, bar0, status);
			return -EIO;
		}

		if (status & PANGO_STATUS_DMA_MODE)
			dma_started = true;
		if (!(status & PANGO_STATUS_FRAME_DONE))
			frame_done_cleared = true;
		if ((dma_started || frame_done_cleared) &&
		    (status & PANGO_STATUS_FRAME_DONE) &&
		    !(status & PANGO_STATUS_DMA_MODE)) {
			*status_out = status;
			return 0;
		}

		msleep(20);
	} while (time_before(jiffies, timeout));

	dev_err(&pdev->dev, "timed out waiting for HDMI frame DMA: status=%#08x\n",
		status);
	pango_log_frame_debug(pdev, bar0, status);
	return -ETIMEDOUT;
}

static void pango_stream_free_slots(struct pango_frame_context *context);

static void pango_frame_context_release(struct kref *references)
{
	struct pango_frame_context *context =
		container_of(references, struct pango_frame_context, references);

	mutex_lock(&context->stream_lock);
	pango_stream_free_slots(context);
	mutex_unlock(&context->stream_lock);
	vfree(context->validation_frame);
	kfree(context->capture_device.name);
	kfree(context->stream_device.name);
	pci_dev_put(context->pdev);
	kfree(context);
}

static void pango_log_frame_timing(struct pango_frame_context *context,
				   const struct pango_frame_timing *timing)
{
	dev_info(&context->pdev->dev,
		 "HDMI timing: seq=%llu lock_us=%llu check_us=%llu fill_us=%llu "
		 "setup_us=%llu submit_us=%llu start_log_us=%llu wait_us=%llu "
		 "dma_copy_us=%llu changed_us=%llu checksum_us=%llu copy_meta_us=%llu "
		 "finish_us=%llu total_us=%llu interval_us=%llu\n",
		 (unsigned long long)context->frame_sequence,
		 (unsigned long long)div_u64(timing->locked_ns - timing->requested_ns, 1000),
		 (unsigned long long)div_u64(timing->checked_ns - timing->locked_ns, 1000),
		 (unsigned long long)div_u64(timing->filled_ns - timing->checked_ns, 1000),
		 (unsigned long long)div_u64(timing->submit_start_ns - timing->filled_ns, 1000),
		 (unsigned long long)div_u64(timing->submitted_ns - timing->submit_start_ns, 1000),
		 (unsigned long long)div_u64(timing->wait_start_ns - timing->submitted_ns, 1000),
		 (unsigned long long)div_u64(timing->waited_ns - timing->wait_start_ns, 1000),
		 (unsigned long long)div_u64(timing->copied_ns - timing->waited_ns, 1000),
		 (unsigned long long)div_u64(timing->changed_ns - timing->copied_ns, 1000),
		 (unsigned long long)div_u64(timing->checksum_ns - timing->changed_ns, 1000),
		 (unsigned long long)div_u64(timing->ready_ns - timing->checksum_ns, 1000),
		 (unsigned long long)div_u64(timing->finished_ns - timing->ready_ns, 1000),
		 (unsigned long long)div_u64(timing->finished_ns - timing->requested_ns, 1000),
		 (unsigned long long)div_u64(timing->interval_ns, 1000));
}

static int pango_capture_frame(struct pango_frame_context *context,
			       void *destination, dma_addr_t dma_address,
			       u8 *pixels, u64 *sequence_out,
			       u32 *checksum_out, u64 *timestamp_out)
{
	struct pci_dev *pdev = context->pdev;
	u32 status;
	u32 submitted_status;
	u32 checksum;
	unsigned int changed_bytes;
	u64 start_ns;
	u64 capture_us;
	u64 frame_timestamp_ns;
	struct pango_frame_timing timing = { 0 };
	bool collect_timing = READ_ONCE(profile);
	int ret;

	if (collect_timing)
		timing.requested_ns = ktime_get_ns();
	ret = mutex_lock_interruptible(&context->capture_lock);
	if (ret)
		return ret;
	if (collect_timing)
		timing.locked_ns = ktime_get_ns();
	if (context->removed) {
		ret = -ENODEV;
		goto unlock;
	}
	if (!destination || !context->validation_frame) {
		dev_err(&pdev->dev, "capture buffer is unavailable\n");
		ret = -EINVAL;
		goto unlock;
	}
	if (context->needs_reset) {
		dev_err(&pdev->dev, "capture blocked after failed DMA; reset the FPGA and re-enumerate PCIe\n");
		ret = -EIO;
		goto unlock;
	}
	if (readl(context->bar0 + 4) != context->frame_bytes ||
	    readl(context->bar0 + 8) != (context->rgb565 ?
		PANGO_RGB565_SIGNATURE : PANGO_FRAME_SIGNATURE)) {
		dev_err(&pdev->dev, "frame format changed; reload after FPGA reset and PCIe enumeration\n");
		ret = -EPROTO;
		goto unlock;
	}

	status = readl(context->bar0);
	if (status == U32_MAX) {
		ret = -ENODEV;
		goto unlock;
	}
	if (status & PANGO_STATUS_DMA_MODE) {
		context->needs_reset = true;
		dev_err(&pdev->dev, "frame bridge is still active before capture: status=%#08x\n", status);
		ret = -EBUSY;
		goto unlock;
	}

	if (collect_timing)
		timing.checked_ns = ktime_get_ns();
	memset(destination, PANGO_FRAME_FILL, context->frame_bytes);
	if (collect_timing)
		timing.filled_ns = ktime_get_ns();
	pci_set_master(pdev);
	dma_wmb();
	writel(lower_32_bits(dma_address), context->bar1 + PANGO_FRAME_ADDR_LOW);
	readl(context->bar0 + PANGO_MMIO_FLUSH);
	writel(upper_32_bits(dma_address), context->bar1 + PANGO_FRAME_ADDR_HIGH);
	readl(context->bar0 + PANGO_MMIO_FLUSH);
	start_ns = ktime_get_ns();
	if (collect_timing)
		timing.submit_start_ns = start_ns;
	writel(1, context->bar1 + PANGO_FRAME_START);
	readl(context->bar0 + PANGO_MMIO_FLUSH);
	submitted_status = readl(context->bar0);
	if (collect_timing)
		timing.submitted_ns = ktime_get_ns();
	dev_info(&pdev->dev, "HDMI frame DMA start submitted: status=%#08x\n",
		 submitted_status);
	pango_log_frame_debug(pdev, context->bar0, submitted_status);

	if (collect_timing)
		timing.wait_start_ns = ktime_get_ns();
	ret = pango_wait_for_frame(pdev, context->bar0, submitted_status, &status);
	timing.waited_ns = ktime_get_ns();
	capture_us = div_u64(timing.waited_ns - start_ns, 1000);
	if (ret) {
		context->needs_reset = true;
		goto stop_dma;
	}

	dma_rmb();
	memcpy(context->validation_frame, destination, context->frame_bytes);
	if (collect_timing)
		timing.copied_ns = ktime_get_ns();
	changed_bytes = pango_frame_changed_bytes(context->validation_frame, context->frame_bytes);
	if (collect_timing)
		timing.changed_ns = ktime_get_ns();
	checksum = pango_frame_checksum(context->validation_frame, context->frame_bytes);
	if (collect_timing)
		timing.checksum_ns = ktime_get_ns();
	if (!changed_bytes) {
		dev_err(&pdev->dev, "HDMI frame DMA completed but buffer is unchanged\n");
		ret = -EIO;
		goto stop_dma;
	}

	context->frame_sequence++;
	if (pixels)
		memcpy(pixels, context->validation_frame, context->frame_bytes);
	if (sequence_out)
		*sequence_out = context->frame_sequence;
	if (checksum_out)
		*checksum_out = checksum;
	frame_timestamp_ns = ktime_get_ns();
	if (timestamp_out)
		*timestamp_out = frame_timestamp_ns;
	if (collect_timing) {
		timing.ready_ns = frame_timestamp_ns;
		if (context->last_frame_timestamp_ns)
			timing.interval_ns = frame_timestamp_ns - context->last_frame_timestamp_ns;
	}
	context->last_frame_timestamp_ns = frame_timestamp_ns;
	dev_info(&pdev->dev,
		 "HDMI frame DMA passed: %u bytes, changed=%u checksum=%#08x status=%#08x frame_seq=%llu capture_us=%llu\n",
		 context->frame_bytes, changed_bytes, checksum, status,
		 (unsigned long long)context->frame_sequence,
		 (unsigned long long)capture_us);
	if (context->frame_sequence == 1)
		dev_info(&pdev->dev, "first 16 bytes: %*ph\n", 16, context->validation_frame);

stop_dma:
	pci_clear_master(pdev);
	if (!ret && collect_timing &&
	    (context->frame_sequence <= 3 || context->frame_sequence % 10 == 0)) {
		timing.finished_ns = ktime_get_ns();
		pango_log_frame_timing(context, &timing);
	}
unlock:
	mutex_unlock(&context->capture_lock);
	return ret;
}

static int pango_capture_open(struct inode *inode, struct file *file)
{
	struct pango_frame_context *context =
		container_of(file->private_data, struct pango_frame_context, capture_device);
	struct pango_frame_reader *reader;

	reader = kzalloc(sizeof(*reader), GFP_KERNEL);
	if (!reader)
		return -ENOMEM;

	kref_get(&context->references);
	reader->context = context;
	mutex_init(&reader->read_lock);
	file->private_data = reader;
	return nonseekable_open(inode, file);
}

static ssize_t pango_capture_read(struct file *file, char __user *buffer,
				  size_t count, loff_t *position)
{
	struct pango_frame_reader *reader = file->private_data;
	size_t header_bytes = reader->context->rgb565 ? 0 : sizeof(PANGO_PGM_HEADER) - 1;
	size_t image_bytes = header_bytes + reader->context->frame_bytes;
	u8 *image;
	ssize_t ret;

	if (!count)
		return 0;
	ret = mutex_lock_interruptible(&reader->read_lock);
	if (ret)
		return ret;

	if (!reader->image) {
		image = vmalloc(image_bytes);
		if (!image) {
			ret = -ENOMEM;
			goto unlock;
		}
		memcpy(image, PANGO_PGM_HEADER, header_bytes);
		ret = pango_capture_frame(reader->context, reader->context->frame,
					  reader->context->dma_address,
					  image + header_bytes, NULL, NULL, NULL);
		if (ret) {
			vfree(image);
			goto unlock;
		}
		reader->image = image;
	}

	ret = simple_read_from_buffer(buffer, count, position, reader->image, image_bytes);
unlock:
	mutex_unlock(&reader->read_lock);
	return ret;
}

static int pango_capture_release(struct inode *inode, struct file *file)
{
	struct pango_frame_reader *reader = file->private_data;

	vfree(reader->image);
	kref_put(&reader->context->references, pango_frame_context_release);
	kfree(reader);
	return 0;
}

static const struct file_operations pango_capture_operations = {
	.owner = THIS_MODULE,
	.open = pango_capture_open,
	.read = pango_capture_read,
	.release = pango_capture_release,
	.llseek = no_llseek,
};

static void pango_stream_free_slots(struct pango_frame_context *context)
{
	unsigned int index;

	for (index = 0; index < PANGO_STREAM_SLOTS; index++) {
		if (context->stream_slots[index].frame) {
			dma_free_coherent(&context->pdev->dev, context->frame_bytes,
					  context->stream_slots[index].frame,
					  context->stream_slots[index].dma_address);
			context->stream_slots[index].frame = NULL;
		}
		context->stream_slots[index].state = PANGO_STREAM_SLOT_FREE;
	}
	context->stream_ready_count = 0;
}

static void pango_stream_reset_slots(struct pango_frame_context *context)
{
	unsigned long flags;
	unsigned int index;

	spin_lock_irqsave(&context->stream_ring_lock, flags);
	for (index = 0; index < PANGO_STREAM_SLOTS; index++)
		context->stream_slots[index].state = PANGO_STREAM_SLOT_FREE;
	context->stream_ready_count = 0;
	context->stream_write_index = 0;
	context->stream_read_index = 0;
	context->stream_error = 0;
	spin_unlock_irqrestore(&context->stream_ring_lock, flags);
}

static int pango_stream_thread(void *data)
{
	struct pango_frame_context *context = data;
	unsigned long flags;
	unsigned int index;
	int ret;

	while (!kthread_should_stop()) {
		if (READ_ONCE(context->removed) || kthread_should_stop())
			break;

		spin_lock_irqsave(&context->stream_ring_lock, flags);
		if (context->stream_ready_count == PANGO_STREAM_SLOTS) {
			spin_unlock_irqrestore(&context->stream_ring_lock, flags);
			wait_event_interruptible_timeout(context->stream_wait,
						       context->stream_ready_count <
						       PANGO_STREAM_SLOTS ||
						       context->removed || kthread_should_stop(),
						       msecs_to_jiffies(20));
			continue;
		}
		index = context->stream_write_index;
		if (context->stream_slots[index].state != PANGO_STREAM_SLOT_FREE) {
			spin_unlock_irqrestore(&context->stream_ring_lock, flags);
			msleep(1);
			continue;
		}
		context->stream_slots[index].state = PANGO_STREAM_SLOT_CAPTURING;
		spin_unlock_irqrestore(&context->stream_ring_lock, flags);

		ret = pango_capture_frame(context, context->stream_slots[index].frame,
					  context->stream_slots[index].dma_address, NULL,
					  &context->stream_slots[index].sequence,
					  &context->stream_slots[index].checksum,
					  &context->stream_slots[index].timestamp_ns);
		spin_lock_irqsave(&context->stream_ring_lock, flags);
		if (ret) {
			context->stream_error = ret;
			context->stream_slots[index].state = PANGO_STREAM_SLOT_FREE;
		} else {
			context->stream_slots[index].state = PANGO_STREAM_SLOT_READY;
			context->stream_ready_count++;
			context->stream_write_index =
				(index + 1) % PANGO_STREAM_SLOTS;
		}
		spin_unlock_irqrestore(&context->stream_ring_lock, flags);
		wake_up_interruptible(&context->stream_wait);
		if (ret)
			break;
	}

	return 0;
}

static int pango_stream_open(struct inode *inode, struct file *file)
{
	struct pango_frame_context *context = container_of(
		file->private_data, struct pango_frame_context, stream_device);
	struct pango_stream_reader *reader;
	int ret = 0;

	reader = kzalloc(sizeof(*reader), GFP_KERNEL);
	if (!reader)
		return -ENOMEM;

	mutex_lock(&context->stream_lock);
	if (context->removed || context->stream_open) {
		ret = context->removed ? -ENODEV : -EBUSY;
	} else {
		context->stream_open = true;
		context->stream_error = 0;
		reader->context = context;
		kref_get(&context->references);
		context->stream_thread = kthread_run(pango_stream_thread, context,
						     "pango_hdmi_stream");
		if (IS_ERR(context->stream_thread)) {
			ret = PTR_ERR(context->stream_thread);
			context->stream_thread = NULL;
			context->stream_open = false;
		}
	}
	mutex_unlock(&context->stream_lock);
	if (ret) {
		if (reader->context)
			kref_put(&context->references, pango_frame_context_release);
		kfree(reader);
		return ret;
	}
	file->private_data = reader;
	return nonseekable_open(inode, file);
}

static int pango_stream_release(struct inode *inode, struct file *file)
{
	struct pango_stream_reader *reader = file->private_data;
	struct pango_frame_context *context = reader->context;
	struct task_struct *stream_thread;

	mutex_lock(&context->stream_lock);
	context->stream_open = false;
	stream_thread = context->stream_thread;
	context->stream_thread = NULL;
	if (stream_thread)
		kthread_stop(stream_thread);
	mutex_unlock(&context->stream_lock);
	reader->slot = NULL;
	reader->record_position = 0;
	pango_stream_reset_slots(context);
	wake_up_interruptible(&context->stream_wait);
	kref_put(&context->references, pango_frame_context_release);
	kfree(reader);
	return 0;
}

static ssize_t pango_stream_read(struct file *file, char __user *buffer,
				size_t count, loff_t *position)
{
	struct pango_stream_reader *reader = file->private_data;
	struct pango_frame_context *context = reader->context;
	struct pango_stream_slot *slot;
	unsigned long flags;
	size_t copied = 0;
	size_t header_position;
	size_t pixel_position;
	size_t copy_count;
	int ret;

	if (!count)
		return 0;

	if (!reader->slot) {
		spin_lock_irqsave(&context->stream_ring_lock, flags);
		while (!context->stream_ready_count && !context->stream_error &&
		       !context->removed) {
			spin_unlock_irqrestore(&context->stream_ring_lock, flags);
			ret = wait_event_interruptible(context->stream_wait,
						       context->stream_ready_count ||
						       context->stream_error || context->removed);
			if (ret)
				return ret;
			spin_lock_irqsave(&context->stream_ring_lock, flags);
		}
		if (context->removed)
			ret = -ENODEV;
		else if (context->stream_error)
			ret = context->stream_error;
		else
			ret = 0;
		if (ret) {
			spin_unlock_irqrestore(&context->stream_ring_lock, flags);
			return ret;
		}

		slot = &context->stream_slots[context->stream_read_index];
		slot->state = PANGO_STREAM_SLOT_READING;
		reader->slot = slot;
		reader->record_position = 0;
		reader->header.magic = cpu_to_le32(PANGO_STREAM_MAGIC);
		reader->header.version = cpu_to_le16(PANGO_STREAM_VERSION);
		reader->header.header_bytes = cpu_to_le16(PANGO_STREAM_HEADER_BYTES);
		reader->header.width = cpu_to_le32(PANGO_FRAME_WIDTH);
		reader->header.height = cpu_to_le32(PANGO_FRAME_HEIGHT);
		reader->header.pixel_bytes = cpu_to_le32(context->frame_bytes);
		reader->header.checksum = cpu_to_le32(slot->checksum);
		reader->header.sequence = cpu_to_le64(slot->sequence);
		reader->header.timestamp_ns = cpu_to_le64(slot->timestamp_ns);
		spin_unlock_irqrestore(&context->stream_ring_lock, flags);
	}

	if (reader->record_position < PANGO_STREAM_HEADER_BYTES) {
		header_position = reader->record_position;
		copy_count = min_t(size_t, count,
					PANGO_STREAM_HEADER_BYTES - header_position);
		if (copy_to_user(buffer,
				 ((u8 *)&reader->header) + header_position, copy_count))
			goto copy_error;
		copied += copy_count;
	}
	if (copied < count) {
		pixel_position = reader->record_position + copied -
			PANGO_STREAM_HEADER_BYTES;
		copy_count = min_t(size_t, count - copied,
					context->frame_bytes - pixel_position);
		if (copy_to_user(buffer + copied,
				 (u8 *)reader->slot->frame + pixel_position, copy_count))
			goto copy_error;
		copied += copy_count;
	}

	reader->record_position += copied;
	if (reader->record_position == PANGO_STREAM_HEADER_BYTES + context->frame_bytes) {
		spin_lock_irqsave(&context->stream_ring_lock, flags);
		reader->slot->state = PANGO_STREAM_SLOT_FREE;
		if (context->stream_ready_count)
			context->stream_ready_count--;
		context->stream_read_index =
			(context->stream_read_index + 1) % PANGO_STREAM_SLOTS;
		spin_unlock_irqrestore(&context->stream_ring_lock, flags);
		reader->slot = NULL;
		reader->record_position = 0;
		wake_up_interruptible(&context->stream_wait);
	}
	*position = 0;
	return copied;

copy_error:
	spin_lock_irqsave(&context->stream_ring_lock, flags);
	reader->slot->state = PANGO_STREAM_SLOT_FREE;
	if (context->stream_ready_count)
		context->stream_ready_count--;
	context->stream_read_index =
		(context->stream_read_index + 1) % PANGO_STREAM_SLOTS;
	spin_unlock_irqrestore(&context->stream_ring_lock, flags);
	reader->slot = NULL;
	reader->record_position = 0;
	wake_up_interruptible(&context->stream_wait);
	return -EFAULT;
}

static __poll_t pango_stream_poll(struct file *file, poll_table *wait)
{
	struct pango_stream_reader *reader = file->private_data;
	struct pango_frame_context *context = reader->context;
	unsigned long flags;
	__poll_t mask = 0;

	poll_wait(file, &context->stream_wait, wait);
	spin_lock_irqsave(&context->stream_ring_lock, flags);
	if (context->stream_ready_count)
		mask |= EPOLLIN | EPOLLRDNORM;
	if (context->stream_error || context->removed)
		mask |= EPOLLERR | EPOLLHUP;
	spin_unlock_irqrestore(&context->stream_ring_lock, flags);
	return mask;
}

static const struct file_operations pango_stream_operations = {
	.owner = THIS_MODULE,
	.open = pango_stream_open,
	.read = pango_stream_read,
	.release = pango_stream_release,
	.poll = pango_stream_poll,
	.llseek = no_llseek,
};

static int pango_hdmi_frame_probe(struct pci_dev *pdev,
				  const struct pci_device_id *id)
{
	struct pango_frame_context *context;
	void __iomem *bar0;
	void __iomem *bar1;
	u32 frame_bytes;
	u32 signature;
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

	ret = pcim_iomap_regions(pdev,
		BIT(PANGO_BAR_STATUS) | BIT(PANGO_BAR_CONTROL),
		"pango_pcie_hdmi_frame_test");
	if (ret)
		return ret;

	bar0 = pcim_iomap_table(pdev)[PANGO_BAR_STATUS];
	bar1 = pcim_iomap_table(pdev)[PANGO_BAR_CONTROL];
	if (!bar0 || !bar1)
		return -ENODEV;

	frame_bytes = readl(bar0 + 4);
	signature = readl(bar0 + 8);
	dev_info(&pdev->dev,
		"HDMI frame capture: bytes=%u signature=%#08x\n",
		frame_bytes, signature);
	if (!((frame_bytes == PANGO_FRAME_BYTES && signature == PANGO_FRAME_SIGNATURE) ||
	      (frame_bytes == PANGO_RGB565_BYTES && signature == PANGO_RGB565_SIGNATURE))) {
		dev_err(&pdev->dev,
			"HDMI frame metadata mismatch: got bytes=%u signature=%#08x; "
			"expected GRAY8 bytes=%u signature=%#08x or RGB565LE bytes=%u signature=%#08x\n",
			frame_bytes, signature, PANGO_FRAME_BYTES, PANGO_FRAME_SIGNATURE,
			PANGO_RGB565_BYTES, PANGO_RGB565_SIGNATURE);
		return -EINVAL;
	}

	ret = dma_set_mask_and_coherent(&pdev->dev, DMA_BIT_MASK(32));
	if (ret) {
		dev_err(&pdev->dev, "32-bit coherent DMA is unavailable: %d\n", ret);
		return ret;
	}

	context = kzalloc(sizeof(*context), GFP_KERNEL);
	if (!context)
		return -ENOMEM;
	context->pdev = pci_dev_get(pdev);
	context->frame_bytes = frame_bytes;
	context->rgb565 = signature == PANGO_RGB565_SIGNATURE;
	dev_info(&pdev->dev, "HDMI pixel format: %s\n", context->rgb565 ? "RGB565LE" : "GRAY8");
	context->bar0 = bar0;
	context->bar1 = bar1;
	kref_init(&context->references);
	mutex_init(&context->capture_lock);
	mutex_init(&context->stream_lock);
	spin_lock_init(&context->stream_ring_lock);
	init_waitqueue_head(&context->stream_wait);
	context->validation_frame = vmalloc(context->frame_bytes);
	if (!context->validation_frame) {
		dev_err(&pdev->dev, "failed to allocate cached validation buffer\n");
		ret = -ENOMEM;
		goto release_context;
	}
	dev_info(&pdev->dev, "HDMI cached validation buffer ready: %u bytes\n",
		 context->frame_bytes);
	context->capture_device.name = kasprintf(GFP_KERNEL, "pango_pcie_frame_%s", pci_name(pdev));
	if (!context->capture_device.name) {
		ret = -ENOMEM;
		goto release_context;
	}
	context->stream_device.name = kasprintf(GFP_KERNEL,
						"pango_pcie_stream_%s", pci_name(pdev));
	if (!context->stream_device.name) {
		ret = -ENOMEM;
		goto release_context;
	}
	context->frame = dma_alloc_coherent(&pdev->dev, context->frame_bytes,
					   &context->dma_address, GFP_KERNEL);
	if (!context->frame) {
		dev_err(&pdev->dev, "failed to allocate HDMI frame buffer\n");
		ret = -ENOMEM;
		goto release_context;
	}

	for (ret = 0; ret < PANGO_STREAM_SLOTS; ret++) {
		context->stream_slots[ret].frame = dma_alloc_coherent(
			&pdev->dev, context->frame_bytes,
			&context->stream_slots[ret].dma_address, GFP_KERNEL);
		if (!context->stream_slots[ret].frame) {
			ret = -ENOMEM;
			goto free_dma;
		}
		context->stream_slots[ret].state = PANGO_STREAM_SLOT_FREE;
	}

	ret = pango_capture_frame(context, context->frame, context->dma_address,
				  NULL, NULL, NULL, NULL);
	if (ret)
		goto free_dma;
	ret = pango_publish_frame(context, context->frame, &context->initial_image);
	if (ret) {
		dev_err(&pdev->dev, "failed to export HDMI frame image: %d\n", ret);
		goto free_dma;
	}

	context->capture_device.minor = MISC_DYNAMIC_MINOR;
	context->capture_device.fops = &pango_capture_operations;
	context->capture_device.parent = &pdev->dev;
	context->capture_device.mode = 0444;
	context->stream_device.minor = MISC_DYNAMIC_MINOR;
	context->stream_device.fops = &pango_stream_operations;
	context->stream_device.parent = &pdev->dev;
	context->stream_device.mode = 0444;
	ret = misc_register(&context->capture_device);
	if (ret) {
		dev_err(&pdev->dev, "failed to register repeat capture device: %d\n", ret);
		sysfs_remove_bin_file(&pdev->dev.kobj, &context->initial_image->image);
		vfree(context->initial_image->data);
		goto free_dma;
	}
	ret = misc_register(&context->stream_device);
	if (ret) {
		dev_err(&pdev->dev, "failed to register stream device: %d\n", ret);
		misc_deregister(&context->capture_device);
		sysfs_remove_bin_file(&pdev->dev.kobj, &context->initial_image->image);
		vfree(context->initial_image->data);
		goto free_dma;
	}

	pci_set_drvdata(pdev, context);
	dev_info(&pdev->dev, "HDMI repeat capture ready: /dev/%s\n",
		 context->capture_device.name);
	dev_info(&pdev->dev, "HDMI stream capture ready: /dev/%s slots=%u record_bytes=%u\n",
		 context->stream_device.name, PANGO_STREAM_SLOTS,
		 (unsigned int)(PANGO_STREAM_HEADER_BYTES + context->frame_bytes));
	return 0;

free_dma:
	pci_clear_master(pdev);
	msleep(100);
	dma_free_coherent(&pdev->dev, context->frame_bytes, context->frame, context->dma_address);
release_context:
	kref_put(&context->references, pango_frame_context_release);
	return ret;
}

static void pango_hdmi_frame_remove(struct pci_dev *pdev)
{
	struct pango_frame_context *context = pci_get_drvdata(pdev);

	if (!context)
		return;

	misc_deregister(&context->capture_device);
	misc_deregister(&context->stream_device);
	mutex_lock(&context->capture_lock);
	context->removed = true;
	pci_clear_master(pdev);
	msleep(100);
	dma_free_coherent(&pdev->dev, context->frame_bytes, context->frame,
				  context->dma_address);
	context->frame = NULL;
	mutex_unlock(&context->capture_lock);
	wake_up_interruptible(&context->stream_wait);
	{
		struct task_struct *stream_thread;

		mutex_lock(&context->stream_lock);
		stream_thread = context->stream_thread;
		context->stream_thread = NULL;
		if (stream_thread)
			kthread_stop(stream_thread);
		mutex_unlock(&context->stream_lock);
	}
	sysfs_remove_bin_file(&pdev->dev.kobj, &context->initial_image->image);
	vfree(context->initial_image->data);
	pci_set_drvdata(pdev, NULL);
	kref_put(&context->references, pango_frame_context_release);
}

static const struct pci_device_id pango_hdmi_frame_ids[] = {
	{ PCI_DEVICE(PANGO_VENDOR_ID, PANGO_DEVICE_ID) },
	{ }
};
MODULE_DEVICE_TABLE(pci, pango_hdmi_frame_ids);

static struct pci_driver pango_hdmi_frame_driver = {
	.name = "pango_pcie_hdmi_frame_test",
	.id_table = pango_hdmi_frame_ids,
	.probe = pango_hdmi_frame_probe,
	.remove = pango_hdmi_frame_remove,
};
module_pci_driver(pango_hdmi_frame_driver);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Pango HDMI frame PCIe DMA capture test");
