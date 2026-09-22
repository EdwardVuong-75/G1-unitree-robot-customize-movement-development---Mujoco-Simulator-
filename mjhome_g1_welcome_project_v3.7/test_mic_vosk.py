"""
test_mic_vosk.py

Standalone diagnostic — bypasses app.py and Flask entirely. Tests just the
microphone capture + Vosk transcription pipeline in isolation, so we can
tell whether a "voice command does nothing" problem is a mic/audio issue
or a command-matching issue further up the stack.

Usage on the G1:
    export VOSK_MODEL_PATH=/home/unitree/g1_voice_model/vosk-model-small-en-us-0.15
    python3 test_mic_vosk.py

It will:
    1. List all available audio input devices (so you can find the right index)
    2. Open the default input device (or MIC_DEVICE_INDEX if set)
    3. Print live partial + final transcripts as you speak

Speak clearly into the mic after it says "Listening..." and watch what
gets printed. If nothing prints even while you're talking, it's a
mic/device problem. If text prints but it's wrong/garbled, it's an
accuracy problem, and the mic itself is confirmed working.
"""

import os
import sys
import json
import queue

import sounddevice as sd
from vosk import Model, KaldiRecognizer

SAMPLE_RATE = 16000

print("=" * 60)
print("Available audio input devices:")
print("=" * 60)
print(sd.query_devices())
print("=" * 60)

default_input = sd.default.device[0]
print(f"Default input device index: {default_input}")

mic_index_env = os.environ.get("MIC_DEVICE_INDEX", "")
device = int(mic_index_env) if mic_index_env else None
print(f"Using device index: {device if device is not None else 'default'}")

model_path = os.environ.get("VOSK_MODEL_PATH", "")
if not model_path or not os.path.isdir(model_path):
    print(f"ERROR: VOSK_MODEL_PATH not set or invalid: '{model_path}'")
    sys.exit(1)

print(f"Loading Vosk model from: {model_path}")
model = Model(model_path)
recognizer = KaldiRecognizer(model, SAMPLE_RATE)
print("Model loaded.")

audio_q = queue.Queue()


def callback(indata, frames, time_info, status):
    if status:
        print(f"[audio status] {status}", file=sys.stderr)
    audio_q.put(bytes(indata))


print("=" * 60)
print("Listening... speak now (Ctrl+C to stop)")
print("=" * 60)

try:
    with sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        blocksize=8000,
        dtype="int16",
        channels=1,
        device=device,
        callback=callback,
    ):
        while True:
            data = audio_q.get()
            if recognizer.AcceptWaveform(data):
                result = json.loads(recognizer.Result())
                text = result.get("text", "")
                if text:
                    print(f"FINAL: \"{text}\"")
            else:
                partial = json.loads(recognizer.PartialResult())
                text = partial.get("partial", "")
                if text:
                    print(f"...partial: \"{text}\"", end="\r")
except KeyboardInterrupt:
    print("\nStopped.")
except Exception as e:
    print(f"\nERROR: {e}")
