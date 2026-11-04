#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

#define HEADER_BYTES 40U
#define FRAME_BYTES (1920U * 1080U)
#define RGB565_BYTES (FRAME_BYTES * 2U)
#define RECORD_BYTES (HEADER_BYTES + RGB565_BYTES)
#define STREAM_MAGIC 0x50475246U
#define SEND_CHUNK_BYTES (64U * 1024U)

static volatile sig_atomic_t stopping;

static uint64_t monotonic_ns(void)
{
	struct timespec timestamp;

	if (clock_gettime(CLOCK_MONOTONIC, &timestamp) < 0) {
		perror("CLOCK_MONOTONIC");
		exit(EXIT_FAILURE);
	}
	return (uint64_t)timestamp.tv_sec * 1000000000ULL + timestamp.tv_nsec;
}

static int start_background(const char *log_path)
{
	int log_file = open(log_path, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0644);
	int input;
	pid_t process;

	if (log_file < 0) {
		perror(log_path);
		return -1;
	}
	process = fork();
	if (process < 0) {
		perror("fork");
		close(log_file);
		return -1;
	}
	if (process > 0) {
		printf("Preview server started: pid=%ld log=%s\n", (long)process, log_path);
		close(log_file);
		return 1;
	}
	if (setsid() < 0) {
		perror("setsid");
		close(log_file);
		return -1;
	}
	input = open("/dev/null", O_RDONLY);
	if (input < 0 || dup2(input, STDIN_FILENO) < 0 ||
	    dup2(log_file, STDOUT_FILENO) < 0 || dup2(log_file, STDERR_FILENO) < 0) {
		perror("Background standard streams");
		if (input >= 0)
			close(input);
		close(log_file);
		return -1;
	}
	if (input > STDERR_FILENO)
		close(input);
	if (log_file > STDERR_FILENO)
		close(log_file);
	return 0;
}

static void stop_server(int signal_number)
{
	(void)signal_number;
	stopping = 1;
}

static uint64_t decode_le(const uint8_t *buffer, size_t bytes)
{
	uint64_t value = 0;
	size_t index;

	for (index = 0; index < bytes; index++)
		value |= (uint64_t)buffer[index] << (index * 8);
	return value;
}

static int read_exact(int descriptor, uint8_t *buffer, size_t bytes)
{
	size_t received = 0;
	struct pollfd event = { .fd = descriptor, .events = POLLIN };

	while (received < bytes && !stopping) {
		ssize_t result;
		int ready = poll(&event, 1, 30000);

		if (ready < 0 && errno == EINTR)
			continue;
		if (ready <= 0) {
			if (!ready)
				errno = ETIMEDOUT;
			return -1;
		}
		result = read(descriptor, buffer + received, bytes - received);
		if (result < 0 && errno == EINTR)
			continue;
		if (result < 0)
			return -1;
		if (!result) {
			if (!received)
				return 0;
			errno = EPROTO;
			return -1;
		}
		received += (size_t)result;
	}
	if (stopping) {
		errno = EINTR;
		return -1;
	}
	return 1;
}

static int validate_header(const uint8_t *record)
{
	if (decode_le(record, 4) != STREAM_MAGIC ||
	    decode_le(record + 4, 2) != 1 ||
	    decode_le(record + 6, 2) != HEADER_BYTES ||
	    decode_le(record + 8, 4) != 1920 ||
	    decode_le(record + 12, 4) != 1080 ||
	    (decode_le(record + 16, 4) != FRAME_BYTES &&
	     decode_le(record + 16, 4) != RGB565_BYTES)) {
		fprintf(stderr, "Invalid stream header: expected 1920x1080 GRAY8 or RGB565LE, version 1\n");
		return -1;
	}
	return 0;
}

static int validate_record(const uint8_t *record)
{
	uint32_t checksum = 2166136261U;
	size_t index;
	size_t record_bytes = HEADER_BYTES + decode_le(record + 16, 4);

	if (validate_header(record))
		return -1;
	for (index = HEADER_BYTES; index < record_bytes; index++)
		checksum = (checksum ^ record[index]) * 16777619U;
	if (checksum != decode_le(record + 20, 4)) {
		fprintf(stderr, "Checksum mismatch: sequence=%" PRIu64 "\n",
			decode_le(record + 24, 8));
		return -1;
	}
	return 0;
}

static int send_exact(int descriptor, const uint8_t *buffer, size_t bytes,
		       size_t *sent_out)
{
	size_t sent = 0;

	*sent_out = 0;
	while (sent < bytes && !stopping) {
		size_t chunk_bytes = bytes - sent;
		ssize_t result;

		if (chunk_bytes > SEND_CHUNK_BYTES)
			chunk_bytes = SEND_CHUNK_BYTES;
		result = send(descriptor, buffer + sent, chunk_bytes,
				      MSG_NOSIGNAL);

		if (result < 0 && errno == EINTR)
			continue;
		if (result <= 0) {
			if (!result)
				errno = EPIPE;
			return -1;
		}
		sent += (size_t)result;
		*sent_out = sent;
	}
	if (stopping)
		errno = EINTR;
	return sent == bytes ? 0 : -1;
}

static void serve_client(int client, const char *source, uint8_t *record,
			 int collect_timing)
{
	struct timeval timeout = { .tv_sec = 30, .tv_usec = 0 };
	uint64_t frames = 0;
	uint64_t previous_sent_ns = 0;
	int input;

	if (setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, &timeout,
		       sizeof(timeout)) < 0) {
		perror("SO_SNDTIMEO");
		return;
	}
	input = open(source, O_RDONLY | O_CLOEXEC);
	if (input < 0) {
		perror(source);
		return;
	}
	printf("Client connected; reading %s\n", source);
	while (!stopping) {
		size_t sent_bytes;
		uint64_t started_ns = collect_timing ? monotonic_ns() : 0;
		uint64_t read_ns;
		uint64_t verified_ns;
		uint64_t sent_ns;
		uint64_t sequence;
		size_t record_bytes;
		int result = read_exact(input, record, HEADER_BYTES);

		if (!result) {
			printf("End of input\n");
			break;
		}
		if (result < 0) {
			if (!stopping)
				perror("Read stream");
			break;
		}
		if (validate_header(record))
			break;
		record_bytes = HEADER_BYTES + decode_le(record + 16, 4);
		result = read_exact(input, record + HEADER_BYTES, record_bytes - HEADER_BYTES);
		if (result != 1) {
			if (!result)
				errno = EPROTO;
			if (!stopping)
				perror("Read stream pixels");
			break;
		}
		read_ns = collect_timing ? monotonic_ns() : 0;
		if (validate_record(record) < 0)
			break;
		verified_ns = collect_timing ? monotonic_ns() : 0;
		if (send_exact(client, record, record_bytes, &sent_bytes) < 0) {
			if (!stopping)
				fprintf(stderr,
					"Send failed: sequence=%" PRIu64 " bytes=%zu/%zu errno=%d (%s)\n",
					decode_le(record + 24, 8), sent_bytes, record_bytes,
					errno, strerror(errno));
			break;
		}
		sent_ns = collect_timing ? monotonic_ns() : 0;
		frames++;
		sequence = decode_le(record + 24, 8);
		if (collect_timing && (frames <= 3 || sequence % 10 == 0))
			printf("Server timing: seq=%" PRIu64 " read_us=%" PRIu64
			       " checksum_us=%" PRIu64 " send_us=%" PRIu64
			       " total_us=%" PRIu64 " interval_us=%" PRIu64 "\n",
			       sequence, (read_ns - started_ns) / 1000,
			       (verified_ns - read_ns) / 1000,
			       (sent_ns - verified_ns) / 1000,
			       (sent_ns - started_ns) / 1000,
			       previous_sent_ns ? (sent_ns - previous_sent_ns) / 1000 : 0);
		previous_sent_ns = sent_ns;
		if (frames <= 3 || frames % 10 == 0)
			printf("Sent frames=%" PRIu64 " sequence=%" PRIu64 "\n",
			       frames, decode_le(record + 24, 8));
	}
	close(input);
	printf("Client finished; sent frames=%" PRIu64 "\n", frames);
}

int main(int argc, char **argv)
{
	const char *source = "/dev/pango_pcie_stream_0002:21:00.0";
	const char *bind_address = "127.0.0.1";
	const char *log_path = "/data/local/tmp/pcie_preview_server.log";
	int background = 0;
	int collect_timing = 0;
	unsigned long port = 9000;
	struct sockaddr_in address = { .sin_family = AF_INET };
	struct sigaction action = { .sa_handler = stop_server };
	uint8_t *record;
	int listener;
	int reuse = 1;
	int argument;

	for (argument = 1; argument < argc; argument++) {
		if (!strcmp(argv[argument], "--help")) {
			printf("Usage: %s [--device PATH] [--bind IPv4] [--port PORT] [--daemon] [--log PATH] [--profile]\n"
			       "--device also accepts a recorded .bin file for replay.\n",
			       argv[0]);
			return 0;
		}
		if (!strcmp(argv[argument], "--daemon")) {
			background = 1;
			continue;
		}
		if (!strcmp(argv[argument], "--profile")) {
			collect_timing = 1;
			continue;
		}
		if (argument + 1 >= argc) {
			fprintf(stderr, "Missing value for %s\n", argv[argument]);
			return 1;
		}
		if (!strcmp(argv[argument], "--device"))
			source = argv[++argument];
		else if (!strcmp(argv[argument], "--bind"))
			bind_address = argv[++argument];
		else if (!strcmp(argv[argument], "--log"))
			log_path = argv[++argument];
		else if (!strcmp(argv[argument], "--port")) {
			char *end;

			errno = 0;
			port = strtoul(argv[++argument], &end, 10);
			if (errno || !*argv[argument] || *end || !port || port > 65535) {
				fprintf(stderr, "Port must be between 1 and 65535\n");
				return 1;
			}
		} else {
			fprintf(stderr, "Unknown option: %s\n", argv[argument]);
			return 1;
		}
	}
	if (inet_pton(AF_INET, bind_address, &address.sin_addr) != 1) {
		fprintf(stderr, "Invalid IPv4 bind address\n");
		return 1;
	}
	address.sin_port = htons((uint16_t)port);
	setvbuf(stdout, NULL, _IOLBF, 0);
	sigemptyset(&action.sa_mask);
	sigaction(SIGINT, &action, NULL);
	sigaction(SIGTERM, &action, NULL);
	signal(SIGPIPE, SIG_IGN);
	listener = socket(AF_INET, SOCK_STREAM, 0);
	if (listener < 0) {
		perror("socket");
		return 1;
	}
	if (setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse)) < 0 ||
	    bind(listener, (struct sockaddr *)&address, sizeof(address)) < 0 ||
	    listen(listener, 1) < 0) {
		perror("Listen socket");
		close(listener);
		return 1;
	}
	record = malloc(RECORD_BYTES);
	if (!record) {
		perror("Frame buffer");
		close(listener);
		return 1;
	}
	if (background) {
		int result = start_background(log_path);

		if (result != 0) {
			free(record);
			close(listener);
			return result < 0 ? 1 : 0;
		}
	}
	printf("PCIe preview v2 listening on %s:%lu, record_bytes=%u pid=%ld\n",
	       bind_address, port, RECORD_BYTES, (long)getpid());
	if (collect_timing)
		printf("Server profiling enabled: units=us; sample=first 3 frames and every tenth sequence\n");
	while (!stopping) {
		int client = accept(listener, NULL, NULL);

		if (client < 0) {
			if (errno == EINTR || errno == ECONNABORTED)
				continue;
			perror("accept");
			break;
		}
		serve_client(client, source, record, collect_timing);
		close(client);
	}
	free(record);
	close(listener);
	printf("Preview server stopped\n");
	return 0;
}
