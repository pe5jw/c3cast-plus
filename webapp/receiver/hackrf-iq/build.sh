#!/bin/sh
# Rebuilds bin/hackrf_iq.exe on Linux with Zig's C compiler (pip install ziglang), against the libusb-1.0.dll and
# libwinpthread-1.dll that are already in bin/ (import libraries are made from their exports). libhackrf is linked in.
#   sh webapp/receiver/hackrf-iq/build.sh        (from the repository root)
set -e
HERE=$(cd "$(dirname "$0")" && pwd); RX="$HERE/.."; WORK=$(mktemp -d)
Z="python3 -m ziglang"
tar xjf "$RX/source/libusb-1.0.27.tar.bz2" -C "$WORK"
mkdir -p "$WORK/inc/libhackrf"; cp "$RX/source/libhackrf-2026.01.3/hackrf.h" "$WORK/inc/libhackrf/"
python3 "$HERE/dll_exports.py" "$RX/bin/libusb-1.0.dll" > "$WORK/libusb-1.0.def"
python3 "$HERE/dll_exports.py" "$RX/bin/libwinpthread-1.dll" > "$WORK/libwinpthread-1.def"
$Z dlltool -m i386:x86-64 -d "$WORK/libusb-1.0.def" -l "$WORK/libusb-1.0.dll.a"
$Z dlltool -m i386:x86-64 -d "$WORK/libwinpthread-1.def" -l "$WORK/libwinpthread-1.dll.a"
$Z cc -target x86_64-windows-gnu -O2 -Wall -I"$WORK/inc" -I"$RX/source/libhackrf-2026.01.3" -I"$WORK/libusb-1.0.27/libusb" \
    -DLIBRARY_RELEASE='"2026.01.3"' -DLIBRARY_VERSION='"0.9"' \
    "$HERE/hackrf_iq.c" "$RX/source/libhackrf-2026.01.3/hackrf.c" "$WORK/libusb-1.0.dll.a" "$WORK/libwinpthread-1.dll.a" \
    -o "$RX/bin/hackrf_iq.exe"
rm -rf "$WORK" "$RX/bin/hackrf_iq.lib" "$RX/bin/hackrf_iq.pdb"      # linker by-products: not shipped
echo "built $RX/bin/hackrf_iq.exe"
