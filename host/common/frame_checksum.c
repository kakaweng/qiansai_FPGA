#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32)
#define PANGO_CHECKSUM_API __declspec(dllexport)
#else
#define PANGO_CHECKSUM_API
#endif

PANGO_CHECKSUM_API uint32_t pango_frame_checksum(const uint8_t *pixels,
					      size_t pixel_bytes)
{
	uint32_t checksum = 2166136261U;
	size_t index;

	for (index = 0; index < pixel_bytes; index++)
		checksum = (checksum ^ pixels[index]) * 16777619U;

	return checksum;
}
