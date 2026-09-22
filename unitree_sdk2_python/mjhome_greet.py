import sys
import time
import wave
import random
import threading
from pathlib import Path

from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient
from unitree_sdk2py.g1.arm.g1_arm_action_client import G1ArmActionClient
from unitree_sdk2py.g1.arm.g1_arm_action_client import action_map

BASE_DIR = Path(__file__).resolve().parent
sys.path.append(str(BASE_DIR / "example/g1/audio"))

from wav import read_wav, play_pcm_stream


GREETING_WAVS = {
    "default": BASE_DIR / "hello_mjhome_16k.wav",
    "1": BASE_DIR / "greeting_1_16k.wav",
    "2": BASE_DIR / "greeting_2_16k.wav",
    "3": BASE_DIR / "greeting_3_16k.wav",
}


def get_wav_duration(wav_path):
    with wave.open(str(wav_path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def select_wav(choice):
    if choice == "random":
        return random.choice([
            GREETING_WAVS["1"],
            GREETING_WAVS["2"],
            GREETING_WAVS["3"],
        ])

    return GREETING_WAVS.get(choice, GREETING_WAVS["default"])


def play_wav(audio, wav_path):
    wav_path = Path(wav_path)

    if not wav_path.exists():
        print("[Audio ERROR] Missing:", wav_path)
        return

    pcm_list, sample_rate, num_channels, is_ok = read_wav(str(wav_path))

    print("[Audio] Read success:", is_ok)
    print("[Audio] Sample rate:", sample_rate)
    print("[Audio] Channels:", num_channels)
    print("[Audio] Play:", wav_path.name)

    if not is_ok or sample_rate != 16000 or num_channels != 1:
        print("[Audio ERROR] WAV must be 16kHz mono:", wav_path)
        return

    duration = get_wav_duration(wav_path)
    app_name = "mjhome_greeting"

    play_pcm_stream(audio, pcm_list, app_name)

    # 等完整播放结束，避免截断
    time.sleep(duration + 1.0)

    audio.PlayStop(app_name)
    print("[Audio] Done.")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 mjhome_greet.py eth0 [default|1|2|3|random]")
        return

    net = sys.argv[1]
    choice = sys.argv[2] if len(sys.argv) >= 3 else "default"
    wav_path = select_wav(choice)

    print("[MJHome] Init DDS...")
    print("[MJHome] Greeting choice:", choice)
    print("[MJHome] WAV:", wav_path)

    ChannelFactoryInitialize(0, net)

    audio = AudioClient()
    audio.SetTimeout(10.0)
    audio.Init()

    try:
        audio.SetVolume(90)
    except Exception as e:
        print("[WARN] SetVolume failed:", e)

    arm = G1ArmActionClient()
    arm.SetTimeout(10.0)
    arm.Init()

    print("[MJHome] Start greeting...")

    audio_thread = threading.Thread(
        target=play_wav,
        args=(audio, wav_path),
        daemon=True,
    )
    audio_thread.start()

    time.sleep(0.3)

    print("[MJHome] Face wave...")
    arm.ExecuteAction(action_map.get("face wave"))

    time.sleep(3.0)

    print("[MJHome] Release arm...")
    arm.ExecuteAction(action_map.get("release arm"))

    audio_thread.join(timeout=20)

    print("[MJHome] Done.")


if __name__ == "__main__":
    main()
