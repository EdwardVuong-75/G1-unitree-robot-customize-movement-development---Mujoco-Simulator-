import sys
import time
import wave
import random
from pathlib import Path

from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient

BASE_DIR = Path(__file__).resolve().parent
sys.path.append(str(BASE_DIR / "example/g1/audio"))

from wav import read_wav, play_pcm_stream

GREETING_WAVS = {
    "1": BASE_DIR / "greeting_1_16k.wav",
    "2": BASE_DIR / "greeting_2_16k.wav",
    "3": BASE_DIR / "greeting_3_16k.wav",
    "4": BASE_DIR / "greeting_4_16k.wav",
}


def get_wav_duration(wav_path):
    with wave.open(str(wav_path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def select_wav(choice):
    if choice == "random":
        return random.choice([GREETING_WAVS["1"], GREETING_WAVS["2"], GREETING_WAVS["3"], GREETING_WAVS["4"]])
    return GREETING_WAVS.get(choice)


def play_wav(audio, wav_path):
    if wav_path is None:
        print("[Audio ERROR] Invalid greeting choice.")
        return

    if not wav_path.exists():
        print("[Audio ERROR] Missing:", wav_path)
        return

    pcm_list, sample_rate, num_channels, is_ok = read_wav(str(wav_path))

    print("[Audio] Play:", wav_path.name)

    if not is_ok or sample_rate != 16000 or num_channels != 1:
        print("[Audio ERROR] WAV must be 16kHz mono:", wav_path)
        return

    duration = get_wav_duration(wav_path)
    app_name = "mjhome_say"

    play_pcm_stream(audio, pcm_list, app_name)
    time.sleep(duration + 1.0)
    audio.PlayStop(app_name)

    print("[Audio] Done.")


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 mjhome_say.py eth0 [1|2|3|random]")
        return

    net = sys.argv[1]
    choice = sys.argv[2]

    print("[MJHome Say] Choice:", choice)

    wav_path = select_wav(choice)

    ChannelFactoryInitialize(0, net)

    audio = AudioClient()
    audio.SetTimeout(10.0)
    audio.Init()

    try:
        audio.SetVolume(90)
    except Exception as e:
        print("[WARN] SetVolume failed:", e)

    play_wav(audio, wav_path)


if __name__ == "__main__":
    main()
