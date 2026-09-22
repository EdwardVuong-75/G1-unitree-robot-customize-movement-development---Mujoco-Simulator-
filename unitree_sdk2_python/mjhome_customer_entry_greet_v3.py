import sys
import time
import cv2
import wave
import threading
from pathlib import Path
from datetime import datetime

from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient
from unitree_sdk2py.g1.arm.g1_arm_action_client import G1ArmActionClient
from unitree_sdk2py.g1.arm.g1_arm_action_client import action_map

BASE_DIR = Path(__file__).resolve().parent
sys.path.append(str(BASE_DIR / "example/g1/audio"))

from wav import read_wav, play_pcm_stream

HELLO_WAV = BASE_DIR / "hello_mjhome_16k.wav"
LOG_FILE = BASE_DIR / "mjhome_customer_entry_log.txt"
SNAPSHOT_DIR = BASE_DIR / "mjhome_customer_snapshots"

# 触发后多少秒内不重复打招呼
COOLDOWN_SECONDS = 5

# 连续检测到多少帧“有人进入”才触发
REQUIRED_DETECTION_FRAMES = 5

# 最小运动面积。camera 3 是 640x480，建议从 2500~6000 之间调
MIN_MOTION_AREA = 3500

# 是否保存触发截图
SAVE_SNAPSHOT = True


def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def get_wav_duration(wav_path):
    with wave.open(str(wav_path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def play_wav(audio, wav_path):
    if not wav_path.exists():
        log(f"[Audio ERROR] Missing: {wav_path}")
        return

    pcm_list, sample_rate, num_channels, is_ok = read_wav(str(wav_path))

    if not is_ok or sample_rate != 16000 or num_channels != 1:
        log(f"[Audio ERROR] WAV must be 16kHz mono: {wav_path}")
        return

    duration = get_wav_duration(wav_path)
    app_name = "mjhome_customer_greeting"

    log(f"[Audio] Play: {wav_path.name}")
    play_pcm_stream(audio, pcm_list, app_name)

    # 必须等待播放完成，否则音频会被截断
    time.sleep(duration + 1.0)

    audio.PlayStop(app_name)
    log("[Audio] Done.")


def greet(audio, arm, frame=None, motion_area=0):
    log(f"[MJHome] Customer entry detected. motion_area={motion_area}")

    SNAPSHOT_DIR.mkdir(exist_ok=True)

    if SAVE_SNAPSHOT and frame is not None:
        snapshot_name = datetime.now().strftime("customer_%Y%m%d_%H%M%S.jpg")
        snapshot_path = SNAPSHOT_DIR / snapshot_name
        cv2.imwrite(str(snapshot_path), frame)
        log(f"[Snapshot] Saved: {snapshot_path}")

    audio_thread = threading.Thread(
        target=play_wav,
        args=(audio, HELLO_WAV),
        daemon=True
    )
    audio_thread.start()

    time.sleep(0.3)

    log("[Action] face wave")
    arm.ExecuteAction(action_map.get("face wave"))

    time.sleep(3.0)

    log("[Action] release arm")
    arm.ExecuteAction(action_map.get("release arm"))

    audio_thread.join(timeout=10)

    log("[MJHome] Greeting finished.")


def draw_motion_debug(frame, contours, motion_area, triggered=False):
    debug = frame.copy()

    for c in contours:
        area = cv2.contourArea(c)
        if area < 300:
            continue
        x, y, w, h = cv2.boundingRect(c)
        cv2.rectangle(debug, (x, y), (x + w, y + h), (255, 255, 255), 2)

    text = f"motion_area={int(motion_area)}"
    cv2.putText(debug, text, (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)

    if triggered:
        cv2.putText(debug, "CUSTOMER DETECTED", (20, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)

    return debug


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 mjhome_customer_entry_greet.py eth0 [camera_id] [min_motion_area]")
        print("Example: python3 mjhome_customer_entry_greet.py eth0 3")
        print("Example: python3 mjhome_customer_entry_greet.py eth0 3 4500")
        return

    net = sys.argv[1]
    camera_id = int(sys.argv[2]) if len(sys.argv) >= 3 else 3
    min_motion_area = int(sys.argv[3]) if len(sys.argv) >= 4 else MIN_MOTION_AREA

    log(f"[MJHome] NetworkInterface: {net}")
    log(f"[MJHome] Camera ID: {camera_id}")
    log(f"[MJHome] MIN_MOTION_AREA: {min_motion_area}")

    if not HELLO_WAV.exists():
        log(f"[ERROR] Missing hello wav: {HELLO_WAV}")
        return

    ChannelFactoryInitialize(0, net)

    audio = AudioClient()
    audio.SetTimeout(10.0)
    audio.Init()

    try:
        audio.SetVolume(90)
    except Exception as e:
        log(f"[WARN] SetVolume failed: {e}")

    arm = G1ArmActionClient()
    arm.SetTimeout(10.0)
    arm.Init()

    cap = cv2.VideoCapture(camera_id)

    if not cap.isOpened():
        log(f"[ERROR] Cannot open camera {camera_id}")
        return

    # 降低分辨率可以提高速度，也减少噪声
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    # 背景建模器：适合检测有人进入画面
    back_sub = cv2.createBackgroundSubtractorMOG2(
        history=120,
        varThreshold=40,
        detectShadows=False
    )

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

    log("[MJHome] Customer entry detection started.")
    log("[MJHome] Please keep robot stationary during detection.")
    log("[MJHome] Press Ctrl+C to stop.")

    # 先读取一些帧，让背景模型稳定
    log("[MJHome] Warming up background model for 2 seconds...")
    start = time.time()
    while time.time() - start < 2.0:
        ok, frame = cap.read()
        if ok:
            back_sub.apply(frame)
        time.sleep(0.05)

    consecutive_detected = 0
    last_greet_time = 0
    frame_id = 0

    try:
        while True:
            ok, frame = cap.read()

            if not ok:
                log("[Camera] Failed to read frame.")
                time.sleep(0.2)
                continue

            frame_id += 1

            # 背景差分
            fg_mask = back_sub.apply(frame)

            # 降噪
            fg_mask = cv2.medianBlur(fg_mask, 5)
            fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, kernel)
            fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel)

            contours, _ = cv2.findContours(
                fg_mask,
                cv2.RETR_EXTERNAL,
                cv2.CHAIN_APPROX_SIMPLE
            )

            motion_area = 0
            valid_contours = []

            for c in contours:
                area = cv2.contourArea(c)
                if area < 300:
                    continue

                x, y, w, h = cv2.boundingRect(c)

                # 过滤太小/太扁的噪声
                if w < 20 or h < 20:
                    continue

                motion_area += area
                valid_contours.append(c)

            detected = motion_area >= min_motion_area

            if detected:
                consecutive_detected += 1
                print(f"[Detection] Motion/person detected. area={int(motion_area)}, count={consecutive_detected}")
            else:
                consecutive_detected = 0

            now = time.time()

            if (
                consecutive_detected >= REQUIRED_DETECTION_FRAMES
                and now - last_greet_time > COOLDOWN_SECONDS
            ):
                debug_frame = draw_motion_debug(frame, valid_contours, motion_area, triggered=True)
                greet(audio, arm, debug_frame, motion_area)
                last_greet_time = time.time()
                consecutive_detected = 0

            # 每隔一段时间保存一张调试图，方便你看检测区域是否正常
            if frame_id % 200 == 0:
                SNAPSHOT_DIR.mkdir(exist_ok=True)
                debug_frame = draw_motion_debug(frame, valid_contours, motion_area, triggered=False)
                debug_path = SNAPSHOT_DIR / "latest_debug.jpg"
                cv2.imwrite(str(debug_path), debug_frame)
                print(f"[Debug] Saved latest debug frame: {debug_path}")

            time.sleep(0.05)

    except KeyboardInterrupt:
        log("[MJHome] Stopped by user.")

    finally:
        cap.release()
        log("[MJHome] Camera released.")


if __name__ == "__main__":
    main()
