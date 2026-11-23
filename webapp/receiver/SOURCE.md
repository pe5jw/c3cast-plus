# Receiver source snapshot

Imported on 2026-11-04 from the QOCAST experimental worktree (branch `experimental`, HEAD `190d1c5`).
The source included that worktree's uncommitted changes to `webplayer.py`,
`tuners.py` and `web/embed.html`; this directory preserves the actual imported code.
`webapp/tuner_assignment.py` was also adapted from the uncommitted worktree.
QOCAST itself has not been modified.
`QOCAST-WORKTREE.patch` records those four imported files' original uncommitted
changes against that HEAD, before the C3CAST adaptations.

The C# tuner service is derived from QOCAST Player / Open Tuner (GPL v3).
Full service source and existing third-party notices accompany it in `tuner-service/`.
The RTL receiver uses DATVision's `rtl_iq` and `datvrx`; see `licenses/DATVision-GPL.txt`.

C3CAST adaptations: worker dispatch, parent-pipe shutdown, dynamic ports including
PicoTuner channel 2, per-receiver sound preferences, and RX 2 muted at page load.
The tuner service binary was rebuilt from the accompanying modified C# sources.

There is no separate receive ffmpeg: the receivers use C3CAST's own tools/ffmpeg (GPL, with libx264
for the browser pictures, built from the sources in tools/ffmpeg/source). Earlier drafts used a ready-made
third-party build; it was removed because its corresponding source could not be supplied. The initial QOCAST
FFmpeg binary had been discarded before that: it enabled nonfree/FDK.

The corresponding sources of the receiver files are in source/ (map: source/SOURCES.txt).

C3CAST-plus (PE5JW) added: `bin/hackrf_iq.exe`, built from `hackrf-iq/hackrf_iq.c` (GPL v2 or later) with libhackrf 2026.01.3
(BSD 3-Clause, `source/libhackrf-2026.01.3/`, `licenses/libhackrf-BSD-3-Clause.txt`) linked in; `hackrf-iq/build.sh` rebuilds it.
Changed: `tuners.py` (HackRF backend, MiniTiouner direct mode for LO 0, RTL-SDR bias-T), `webplayer.py` (band profiles, bias-T and
HackRF settings), `usb_devices.py` (HackRF detection) and `web/embed.html` (the new switches). The tuner service is unchanged.
The DATVision executables are byte-for-byte those of DATVision 1.0.0, whose source is source/DATVision-1.0.0-source.zip.
