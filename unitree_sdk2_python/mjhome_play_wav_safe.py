import sys
import time
import wave
from pathlib import Path

from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient

# 让当前脚本能导入官方 example/g1/audio/wav.py
BASE_DIR = Path(__file__).resolve().parent
sys.path.append(str(BASE_DIR / "example/g1/audio"))

from wav import read_wav, play_pcm_stream


def get_wav_duration(wav_path):
    with wave.open(wav_path, "rb") as w:
        frames = w.getnframes()
        rate = w.getframerate()
        return frames / float(rate)


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 mjhome_play_wav_safe.py eth0 hello_mjhome_16k.wav")
        return

    net = sys.argv[1]
    wav_path = sys.argv[2]

    if not Path(wav_path).exists():
        print("[ERROR] WAV not found:", wav_path)
        return

    ChannelFactoryInitialize(0, net)

    audio = AudioClient()
    audio.SetTimeout(10.0)
    audio.Init()

    try:
        audio.SetVolume(90)
    except Exception as e:
        print("[WARN] SetVolume failed:", e)

    pcm_list, sample_rate, num_channels, is_ok = read_wav(wav_path)

    print("[DEBUG] Read success:", is_ok)
    print("[DEBUG] Sample rate:", sample_rate)
    print("[DEBUG] Channels:", num_channels)
    print("[DEBUG] PCM byte length:", len(pcm_list))

    if not is_ok or sample_rate != 16000 or num_channels != 1:
        print("[ERROR] WAV must be 16kHz mono")
        return

    duration = get_wav_duration(wav_path)
    print("[DEBUG] Duration:", duration)

    app_name = "mjhome"
    play_pcm_stream(audio, pcm_list, app_name)

    # 关键：等待音频播放完成，不要马上 PlayStop
    time.sleep(duration + 1.0)

    audio.PlayStop(app_name)
    print("[Audio] Done.")


if __name__ == "__main__":
    main()
