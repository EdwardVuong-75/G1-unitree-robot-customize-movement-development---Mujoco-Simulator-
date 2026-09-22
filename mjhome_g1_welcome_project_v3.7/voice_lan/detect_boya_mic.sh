#!/usr/bin/env bash
set -e

cd ~/mjhome_g1_welcome_project/voice_lan
mkdir -p tmp/boya_test

echo "=== USB devices ==="
lsusb
echo

echo "=== ALSA capture devices ==="
arecord -l
echo

echo "=== Parsing capture candidates ==="

arecord -l > tmp/boya_test/arecord_l.txt

python3 - <<'PY'
import re
from pathlib import Path

text = Path("tmp/boya_test/arecord_l.txt").read_text(errors="ignore")
candidates = []

for line in text.splitlines():
    m = re.search(r"card\s+(\d+):.*device\s+(\d+):", line)
    if not m:
        continue

    card = int(m.group(1))
    dev = int(m.group(2))
    lower = line.lower()

    # Skip the Jetson internal APE virtual routes by default.
    # BOYA/USB mic usually appears as a different USB Audio card.
    score = 0
    if "usb" in lower: score += 5
    if "boya" in lower: score += 10
    if "wireless" in lower: score += 5
    if "microphone" in lower: score += 5
    if "audio" in lower: score += 2
    if "ape" in lower or "tegra-dlink" in lower or "admaif" in lower:
        score -= 10

    candidates.append((score, card, dev, line))

candidates.sort(reverse=True)

print("Candidates:")
for score, card, dev, line in candidates:
    print(f"  plughw:{card},{dev} | score={score} | {line}")

Path("tmp/boya_test/candidates.txt").write_text(
    "\n".join(f"plughw:{card},{dev}" for score, card, dev, line in candidates if score > -5)
)
PY

echo
echo "=== Candidate devices ==="
cat tmp/boya_test/candidates.txt || true
echo

if [ ! -s tmp/boya_test/candidates.txt ]; then
    echo "No usable capture candidates found."
    echo "If BOYA receiver is plugged in, unplug/replug it and run:"
    echo "  lsusb"
    echo "  arecord -l"
    exit 1
fi

echo
echo "Now testing each candidate. Speak loudly into the BOYA transmitter."
echo

BEST=""
BEST_RMS=0

while read -r DEV; do
    [ -z "$DEV" ] && continue

    SAFE_NAME=$(echo "$DEV" | tr ':,' '__')
    OUT="tmp/boya_test/${SAFE_NAME}.wav"

    echo "========================================"
    echo "Testing $DEV"
    echo "Speak now: 'where are the pods'"

    if timeout 8 arecord -D "$DEV" -f S16_LE -r 16000 -c 1 -d 5 -q "$OUT" 2>"tmp/boya_test/${SAFE_NAME}.err"; then
        RESULT=$(python3 - "$OUT" <<'PY'
import wave, audioop, os, sys

p = sys.argv[1]
wf = wave.open(p, "rb")
data = wf.readframes(wf.getnframes())
wf.close()

rms = audioop.rms(data, 2) if data else 0
peak = audioop.max(data, 2) if data else 0
size = os.path.getsize(p)

print(f"{rms} {peak} {size}")
PY
)
        RMS=$(echo "$RESULT" | awk '{print $1}')
        PEAK=$(echo "$RESULT" | awk '{print $2}')
        SIZE=$(echo "$RESULT" | awk '{print $3}')

        echo "OK $DEV size=$SIZE rms=$RMS peak=$PEAK"

        if [ "$RMS" -gt "$BEST_RMS" ]; then
            BEST_RMS="$RMS"
            BEST="$DEV"
        fi
    else
        echo "FAILED $DEV"
        cat "tmp/boya_test/${SAFE_NAME}.err" || true
    fi
done < tmp/boya_test/candidates.txt

echo
echo "========================================"
echo "Best candidate: $BEST"
echo "Best RMS: $BEST_RMS"

if [ -n "$BEST" ]; then
    echo "export MIC_DEVICE=$BEST" > boya_mic_device.env
    echo "Saved: boya_mic_device.env"
    cat boya_mic_device.env
else
    echo "No working mic candidate found."
    exit 1
fi
