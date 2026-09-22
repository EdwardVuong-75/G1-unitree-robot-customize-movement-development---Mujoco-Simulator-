#!/usr/bin/env bash
set -e

cd ~/mjhome_g1_welcome_project

WAV="audio/phrase2.wav"

if [ ! -f "$WAV" ]; then
    echo "Missing $WAV"
    exit 1
fi

echo "=== ALSA playback devices ==="
aplay -l || true
echo

mapfile -t DEVICES < <(aplay -l | awk -F'[:, ]+' '/^card/ {print "plughw:"$2","$6}' | sort -u)

echo "=== Testing default output ==="
aplay "$WAV" || true
read -p "Did you hear sound from default? [y/N] " ans
if [[ "$ans" == "y" || "$ans" == "Y" ]]; then
    echo "default" > working_audio_device.txt
    echo "Working audio device: default"
    exit 0
fi

for dev in "${DEVICES[@]}"; do
    echo
    echo "=== Testing $dev ==="
    aplay -D "$dev" "$WAV" || true
    read -p "Did you hear sound from $dev? [y/N] " ans
    if [[ "$ans" == "y" || "$ans" == "Y" ]]; then
        echo "$dev" > working_audio_device.txt
        echo "Working audio device: $dev"
        exit 0
    fi
done

echo
echo "No working audio device found by this test."
echo "Next test old mjhome_say.py."
