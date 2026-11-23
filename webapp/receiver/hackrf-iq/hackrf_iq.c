/* hackrf_iq: minimal binary-safe HackRF pipe source for C3CAST-plus, after DATVision's rtl_iq.
 *
 * Writes interleaved signed 8-bit I/Q (datvrx --format cs8) to standard output, which is switched to binary mode on
 * Windows (hackrf_transfer -r - does not do that, so its stream is corrupted there). With --watch-stdin it stops when
 * its standard input closes (C3CAST closes the pipe), so the HackRF is stopped and closed cleanly, antenna power off.
 *
 * usage: hackrf_iq frequency_Hz sample_rate_Hz lna_dB vga_dB amp(0|1) antenna_power(0|1) [--watch-stdin]
 *
 * Links libhackrf (BSD-3-Clause, Great Scott Gadgets). This file: GPL-2.0-or-later.
 */
#include <libhackrf/hackrf.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#include <windows.h>
#define SLEEP_MS(ms) Sleep(ms)
#else
#include <pthread.h>
#include <unistd.h>
#define SLEEP_MS(ms) usleep((ms) * 1000)
#endif

static volatile sig_atomic_t stopping = 0;
static volatile int write_failed = 0;

static void request_stop(int sig) { (void) sig; stopping = 1; }

static int deliver(hackrf_transfer* transfer)
{
	if (stopping)
		return -1;                                    /* nonzero: libhackrf stops streaming */
	size_t count = (size_t) transfer->valid_length;
	if (fwrite(transfer->buffer, 1, count, stdout) != count || fflush(stdout) != 0) {
		write_failed = 1;                             /* the reader went away */
		stopping = 1;
		return -1;
	}
	return 0;
}

#ifdef _WIN32
static DWORD WINAPI watch_stdin(LPVOID unused)
#else
static void* watch_stdin(void* unused)
#endif
{
	(void) unused;
	while (getchar() != EOF) {
	}
	stopping = 1;
	return 0;
}

static int check(int result, const char* what)
{
	if (result != HACKRF_SUCCESS)
		fprintf(stderr, "hackrf_iq: %s failed: %s (%d)\n", what, hackrf_error_name((enum hackrf_error) result), result);
	return result != HACKRF_SUCCESS;
}

int main(int argc, char** argv)
{
	if (argc < 7 || argc > 8 || (argc == 8 && strcmp(argv[7], "--watch-stdin") != 0)) {
		fprintf(stderr, "usage: hackrf_iq frequency_Hz sample_rate_Hz lna_dB vga_dB amp(0|1) antenna_power(0|1) [--watch-stdin]\n");
		return 1;
	}
	const uint64_t frequency = strtoull(argv[1], NULL, 10);
	const double rate = strtod(argv[2], NULL);
	const unsigned lna = (unsigned) strtoul(argv[3], NULL, 10);
	const unsigned vga = (unsigned) strtoul(argv[4], NULL, 10);
	const int amp = atoi(argv[5]) != 0;
	const int antenna = atoi(argv[6]) != 0;
	const int watch = argc == 8;
	if (frequency < 1000000ULL || frequency > 7250000000ULL || rate < 2e6 || rate > 20e6 || lna > 40 || vga > 62) {
		fprintf(stderr, "hackrf_iq: frequency 1 MHz-7.25 GHz, rate 2-20 Msps, LNA 0-40 dB, VGA 0-62 dB\n");
		return 1;
	}
#ifdef _WIN32
	_setmode(_fileno(stdout), _O_BINARY);
	_setmode(_fileno(stdin), _O_BINARY);
#endif
	signal(SIGINT, request_stop);
	signal(SIGTERM, request_stop);
#ifdef SIGBREAK
	signal(SIGBREAK, request_stop);
#endif
#ifdef SIGPIPE
	signal(SIGPIPE, SIG_IGN);                             /* a closed reader is seen by fwrite instead */
#endif
	if (check(hackrf_init(), "hackrf_init"))
		return 1;
	hackrf_device* device = NULL;
	const char* serial = getenv("C3CAST_HACKRF_SERIAL");  /* optional: one HackRF of several */
	if (check(serial && *serial ? hackrf_open_by_serial(serial, &device) : hackrf_open(&device), "hackrf_open")) {
		hackrf_exit();
		return 1;
	}
	int failed = check(hackrf_set_sample_rate(device, rate), "set sample rate") ||
		check(hackrf_set_baseband_filter_bandwidth(device, hackrf_compute_baseband_filter_bw((uint32_t) (rate * 0.75))), "set baseband filter") ||
		check(hackrf_set_freq(device, frequency), "set frequency") ||
		check(hackrf_set_amp_enable(device, (uint8_t) amp), "set RF amp") ||
		check(hackrf_set_antenna_enable(device, (uint8_t) antenna), "set antenna power") ||
		check(hackrf_set_lna_gain(device, lna - lna % 8), "set LNA gain") ||
		check(hackrf_set_vga_gain(device, vga - vga % 2), "set VGA gain");
	if (!failed) {
		fprintf(stderr, "hackrf_iq: %llu Hz, %.0f samples/s, LNA %u dB, VGA %u dB, amp %s, antenna power %s; binary stdout\n",
			(unsigned long long) frequency, rate, lna - lna % 8, vga - vga % 2, amp ? "on" : "off", antenna ? "on" : "off");
		if (watch) {
#ifdef _WIN32
			HANDLE thread = CreateThread(NULL, 0, watch_stdin, NULL, 0, NULL);
			if (thread)
				CloseHandle(thread);
#else
			pthread_t thread;
			if (pthread_create(&thread, NULL, watch_stdin, NULL) == 0)
				pthread_detach(thread);
#endif
		}
		failed = check(hackrf_start_rx(device, deliver, NULL), "start receiving");
		while (!failed && !stopping && hackrf_is_streaming(device) == HACKRF_TRUE)
			SLEEP_MS(50);
		hackrf_stop_rx(device);
	}
	hackrf_set_antenna_enable(device, 0);                 /* never leave voltage on the antenna input */
	hackrf_close(device);
	hackrf_exit();
	return failed ? 1 : 0;
}
