#!/usr/bin/env bash
set -e

cd ~/mjhome_g1_welcome_project/voice_lan
mkdir -p tmp/mic_test

echo "We will test plughw:1,0 to plughw:1,21."
echo "During each test, please speak loudly or clap near the robot."
echo

for dev in $(seq 0 21); do
    OUT="tmp/mic_test/card1_dev${dev}.wav"
    ERR="tmp/mic_test/card1_dev${dev}.err"

    echo "========================================"
    echo "Testing MIC_DEVICE=plughw:1,$dev"
    echo "Please speak loudly now..."

    if timeout 6 arecord -D "plughw:1,$dev" -f S16_LE -r 16000 -c 1 -d 3 -q "$OUT" 2>"$ERR"; then
        python3 - "$OUT" <<'PY'
import wave
import audioop
import os
import sys

p = sys.argv[1]
try:
    wf = wave.open(p, "rb")
    data = wf.readframes(wf.getnframes())
    wf.close()

    rms = audioop.rms(data, 2) if data else 0
    peak = audioop.max(data, 2) if data else 0
    size = os.path.getsize(p)

    print(f"OK file={p} size={size} rms={rms} peak={peak}")

    if rms > 300 or peak > 2000:
        print("POSSIBLE_MIC_SIGNAL=YES")
    else:
        print("POSSIBLE_MIC_SIGNAL=LOW_OR_SILENT")
except Exception as e:
    print("PYTHON_ANALYZE_ERROR:", e)
PY
    else
        echo "FAILED plughw:1,$dev"
        cat "$ERR" || true
    fi

    sleep 0.3
done

echo
echo "Done. Check which device has POSSIBLE_MIC_SIGNAL=YES and high rms/peak."
