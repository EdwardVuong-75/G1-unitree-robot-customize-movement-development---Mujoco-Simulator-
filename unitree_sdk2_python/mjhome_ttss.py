import sys
import time

from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 mjhome_tts.py eth0 [text] [speaker_id]")
        return

    net = sys.argv[1]
    text = sys.argv[2] if len(sys.argv) > 2 else "Hello, I am M J Home Robot."
    speaker_id = int(sys.argv[3]) if len(sys.argv) > 3 else 0

    ChannelFactoryInitialize(0, net)

    audio = AudioClient()
    audio.SetTimeout(10.0)
    audio.Init()

    try:
        code = audio.SetVolume(80)
        print("SetVolume code:", code)
    except Exception as e:
        print("SetVolume failed:", e)

    print("[TTS]", text)
    code = audio.TtsMaker(text, speaker_id)
    print("TtsMaker code:", code)

    time.sleep(5)


if __name__ == "__main__":
    main()