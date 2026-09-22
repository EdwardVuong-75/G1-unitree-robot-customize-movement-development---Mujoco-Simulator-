import sys
import time
import cv2
import wave
import threading
from pathlib import Path

from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient
from unitree_sdk2py.g1.arm.g1_arm_action_client import G1ArmActionClient
from unitree_sdk2py.g1.arm.g1_arm_action_client import action_map

BASE_DIR = Path(__file__).resolve().parent
sys.path.append(str(BASE_DIR / "example/g1/audio"))

from wav import read_wav, play_pcm_stream

HELLO_WAV = BASE_DIR / "hello_mjhome_16k.wav"

# 检测到人后，20秒内不重复打招呼
COOLDOWN_SECONDS = 20

# 连续检测到多少帧人脸后触发
REQUIRED_DETECTION_FRAMES = 3


def get_wav_duration(wav_path):
    with wave.open(str(wav_path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def play_wav(audio, wav_path):
    if not wav_path.exists():
        print("[Audio ERROR] Missing:", wav_path)
        return

    pcm_list, sample_rate, num_channels, is_ok = read_wav(str(wav_path))

    print("[Audio] Read success:", is_ok)
    print("[Audio] Sample rate:", sample_rate)
    print("[Audio] Channels:", num_channels)

    if not is_ok or sample_rate != 16000 or num_channels != 1:
        print("[Audio ERROR] WAV must be 16kHz mono:", wav_path)
        return

    duration = get_wav_duration(wav_path)
    app_name = "mjhome_greeting"

    print("[Audio] Play:", wav_path.name)
    play_pcm_stream(audio, pcm_list, app_name)

    # 必须等待播放完成，否则会截断
    time.sleep(duration + 1.0)

    audio.PlayStop(app_name)
    print("[Audio] Done.")


def greet(audio, arm):
    print("[MJHome] Greeting triggered.")

    audio_thread = threading.Thread(
        target=play_wav,
        args=(audio, HELLO_WAV),
        daemon=True
    )
    audio_thread.start()

    # 语音先开始一点，再挥手
    time.sleep(0.3)

    print("[Action] face wave")
    arm.ExecuteAction(action_map.get("face wave"))

    time.sleep(3.0)

    print("[Action] release arm")
    arm.ExecuteAction(action_map.get("release arm"))

    audio_thread.join(timeout=10)

    print("[MJHome] Greeting finished.")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 mjhome_person_greet.py eth0 [camera_id]")
        print("Example: python3 mjhome_person_greet.py eth0 3")
        return

    net = sys.argv[1]

    # 默认 camera 3，因为你刚才确认 camera 3 画面最好
    camera_id = int(sys.argv[2]) if len(sys.argv) >= 3 else 3

    print("[MJHome] NetworkInterface:", net)
    print("[MJHome] Camera ID:", camera_id)

    if not HELLO_WAV.exists():
        print("[ERROR] Missing hello wav:", HELLO_WAV)
        print("Please make sure hello_mjhome_16k.wav exists in ~/unitree_sdk2_python")
        return

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

    face_cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    face_detector = cv2.CascadeClassifier(face_cascade_path)

    if face_detector.empty():
        print("[ERROR] Cannot load face detector:", face_cascade_path)
        return

    cap = cv2.VideoCapture(camera_id)

    if not cap.isOpened():
        print(f"[ERROR] Cannot open camera {camera_id}")
        return

    print("[MJHome] Detection started.")
    print("[MJHome] Looking for a face/person...")
    print("[MJHome] Press Ctrl+C to stop.")

    consecutive_detected = 0
    last_greet_time = 0

    try:
        while True:
            ok, frame = cap.read()

            if not ok:
                print("[Camera] Failed to read frame.")
                time.sleep(0.2)
                continue

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            faces = face_detector.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=5,
                minSize=(50, 50)
            )

            if len(faces) > 0:
                consecutive_detected += 1
                print(f"[Detection] Face detected. count={consecutive_detected}")
            else:
                consecutive_detected = 0

            now = time.time()

            if (
                consecutive_detected >= REQUIRED_DETECTION_FRAMES
                and now - last_greet_time > COOLDOWN_SECONDS
            ):
                greet(audio, arm)
                last_greet_time = time.time()
                consecutive_detected = 0

            time.sleep(0.05)

    except KeyboardInterrupt:
        print("\n[MJHome] Stopped by user.")

    finally:
        cap.release()
        print("[MJHome] Camera released.")


if __name__ == "__main__":
    main()
