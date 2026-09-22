import os
import subprocess
import threading
import time
import random
import json as _json
import urllib.request
from datetime import datetime
from pathlib import Path

import mediapipe as mp

import cv2
import numpy as np
from flask import Flask, Response, jsonify, render_template, request

last_face = 0
Face_cooldown = 10 # second between greetings

try:
    import pyrealsense2 as rs
    HAS_REALSENSE = True
except Exception:
    rs = None
    HAS_REALSENSE = False

BASE_DIR = Path(__file__).resolve().parent
AUDIO_DIR = BASE_DIR / "audio"

NETWORK_INTERFACE = os.environ.get("NETWORK_INTERFACE", "eth0")
PORT = int(os.environ.get("PORT", "5010"))
DEFAULT_CAMERA_ID = int(os.environ.get("DEFAULT_CAMERA_ID", "3"))
AUDIO_DEVICE = os.environ.get("AUDIO_DEVICE", "")
HAAR_CASCADE_PATH = os.environ.get("HAAR_CASCADE_PATH", "")

# 不复制、不修改旧项目；只调用旧项目里已经验证过的底层脚本
G1_WALK_CLI = Path(os.environ.get("G1_WALK_CLI", "/home/unitree/unitree_sdk2_python/g1_walk_cli.py"))
MJHOME_SAY_SCRIPT = Path(os.environ.get("MJHOME_SAY_SCRIPT", "/home/unitree/unitree_sdk2_python/mjhome_say2.py"))
# CLI that fires one of G1ArmActionClient's preset animations (see g1_arm_cli.py).
G1_ARM_CLI = Path(os.environ.get("G1_ARM_CLI", "/home/unitree/unitree_sdk2_python/g1_arm_cli.py"))

G1_TTS_CLI = Path(os.environ.get("G1_TTS_CLI", "/home/unitree/unitree_sdk2_python/mjhome_ttss.py"))

app = Flask(__name__)

log_lines = []
MAX_LOG_LINES = 500
motion_enabled = False

camera_lock = threading.Lock()
camera_mode = "v4l2"
camera_id = DEFAULT_CAMERA_ID

face_detection_enabled = False
haar_face_cascade = None

# --- Pose / skeleton tracking state ---
pose_detection_enabled = False
pose_lock = threading.Lock()
pose_detector = None
mp_pose = mp.solutions.pose
mp_drawing = mp.solutions.drawing_utils
mp_drawing_styles = mp.solutions.drawing_styles

# --- Gesture -> preset animation mapping ---
# Only gestures that are reliably detectable from MediaPipe Pose's 33 body
# landmarks (shoulders/elbows/wrists/hips -- no fingers, no face detail) are
# wired up here. Presets like "heart", "x-ray", the kiss variants, and the
# wave variants need either finger-shape recognition (MediaPipe Hands) or
# motion/velocity tracking (waving, clapping) that isn't implemented yet --
# see the chat notes for what's out of scope in this pass.
GESTURE_TO_ACTION = {
    "hands_up": "hands up",
    "right_hand up": "right hand up",
    "shake_hand": "shake hand",
    "hug": "hug",
}

ARM_ACTION_NAMES = {
    "release arm", "two-hand kiss", "left kiss", "right kiss", "hands up",
    "clap", "high five", "hug", "heart", "right heart", "reject",
    "right hand up", "x-ray", "face wave", "high wave", "shake hand",
}

ACTION_COOLDOWN = 10.0  # seconds between face-triggered actions

RAISE_MARGIN = 0.08          # wrist must be this much higher (normalized) than shoulder
REACH_MARGIN = 0.18          # normalized horizontal reach for "shake hand"
STREAK_NEEDED = 6            # consecutive frames a gesture must hold before firing
ARM_COMMAND_COOLDOWN = ACTION_COOLDOWN   # seconds between arm commands (presets take a couple seconds to play)

_gesture_streak_value = "neutral"
_gesture_streak_count = 0
_last_arm_command_time = 0.0
_last_arm_action_sent = "neutral"  # last preset action name dispatched, "neutral" = released/idle

# --- Continuous RIGHT-ARM-ONLY mimicry (streamed to arm_mimic_server_right.py,
# a separate persistent process -- see chat notes for why this can't share
# app.py's process/import context with the preset ArmActionClient path
# above). Mutually exclusive with the gesture-preset system: both drive
# the right arm via different channels and would fight if both ran. ---
MIMIC_SERVER_URL = "http://127.0.0.1:5011"
mimic_enabled = False
_last_mimic_push = 0.0
MIMIC_PUSH_INTERVAL = 1.0 / 20  # 20Hz push rate; the control loop itself runs at 50Hz
LANDMARK_VISIBILITY_MIN = 0.6
# MediaPipe's z (depth) estimate is much noisier/less stable than x/y,
# and it's what pitch is derived from. Damping it reduces how hard a
# single noisy frame can swing the pitch target -- this is on top of,
# not instead of, the hard per-tick slew limit in arm_mimic_server_right.py.
PITCH_Z_DAMPING = 0.5

# Must match G1JointIndex values in arm_mimic_server_right.py
J_R_SHOULDER_PITCH, J_R_SHOULDER_ROLL, J_R_ELBOW, J_R_Wrist_Pitch = 22, 23, 25, 26

PHRASES = {
    "1": "Welcome to MJ Home, your cozy and future-ready space.",
    "2": "We are MJ Home, building a sustainable, long-term future for everyone.",
    "3": "MJ Home is proud to serve our community and help transform New Zealand.",
    "4": "Somebody get these beggars out of here",
}


def add_log(message: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    log_lines.append(line)
    if len(log_lines) > MAX_LOG_LINES:
        del log_lines[: len(log_lines) - MAX_LOG_LINES]


def init_haar_cascade():
    global haar_face_cascade
    if haar_face_cascade is not None:
        return True

    candidate_paths = []
    if HAAR_CASCADE_PATH:
        candidate_paths.append(Path(HAAR_CASCADE_PATH))
    candidate_paths.append(BASE_DIR / "haarcascade_frontalface_default.xml")
    try:
        candidate_paths.append(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
    except Exception:
        pass

    for path in candidate_paths:
        if path and path.is_file():
            cascade = cv2.CascadeClassifier(str(path))
            if not cascade.empty():
                haar_face_cascade = cascade
                add_log(f"Haar cascade loaded: {path}")
                return True

    add_log("Haar cascade could not be loaded (file not found or invalid).")
    return False


def detect_faces_haar(bgr_frame):
    """Detect faces in a BGR frame with the Haar cascade. Returns list of (x, y, w, h)."""
    if not init_haar_cascade():
        return []
    gray = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    faces = haar_face_cascade.detectMultiScale(
        gray,
        scaleFactor=1.3,
        minNeighbors=5,
        minSize=(40, 40)
    )
    return faces

def draw_faces(frame):
    """If face detection is enabled, draw boxes around detected faces in-place."""
    global last_face

    if not face_detection_enabled:
        return frame
    try:
        faces = detect_faces_haar(frame)
        for (x, y, w, h) in faces:
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

        #Add speaking and movement when detect a face
        if len(faces) > 0:
            now = time.time()
            if now - last_face > ACTION_COOLDOWN:
                choice = random.choice([ "1", "2", "3"])
                gesture = random.choice(["left kiss", "right kiss", "face wave", "high wave", "shake hand"])
                say_phrase(choice)
                if not mimic_enabled:
                    arm_command(gesture)
                last_face = now

        cv2.putText(
            frame,
            f"Faces: {len(faces)}",
            (20, 76),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 0),
            2,
        )
    except Exception as e:
        add_log(f"Haar cascade error: {e}")
        cv2.putText(frame, "Haar cascade error", (20, 76),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
    return frame


def init_pose_detector():
    global pose_detector
    if pose_detector is not None:
        return True
    try:
        pose_detector = mp_pose.Pose(
            static_image_mode=False,
            model_complexity=1,
            enable_segmentation=False,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        add_log("MediaPipe Pose detector initialized")
        return True
    except Exception as e:
        add_log(f"Failed to initialize MediaPipe Pose: {e}")
        pose_detector = None
        return False


def _visible(lm):
    return lm.visibility is None or lm.visibility > 0.5


def classify_body_gesture(landmarks):
    """
    Map one frame of MediaPipe Pose landmarks to one of GESTURE_TO_ACTION's
    keys, or "neutral". Body-pose-only detection (no fingers, no motion
    history), so this covers static arm postures, not hand shapes or waves.
    """
    L = mp_pose.PoseLandmark
    ls = landmarks[L.LEFT_SHOULDER.value]
    le = landmarks[L.LEFT_ELBOW.value]
    lw = landmarks[L.LEFT_WRIST.value]
    rs_ = landmarks[L.RIGHT_SHOULDER.value]
    re_ = landmarks[L.RIGHT_ELBOW.value]
    rw = landmarks[L.RIGHT_WRIST.value]
    lhip = landmarks[L.LEFT_HIP.value]
    rhip = landmarks[L.RIGHT_HIP.value]

    def vis(*lms):
        return all(_visible(l) for l in lms)

    left_up = vis(ls, lw) and (ls.y - lw.y) > RAISE_MARGIN
    right_up = vis(rs_, rw) and (rs_.y - rw.y) > RAISE_MARGIN

    if left_up and right_up:
        return "hands_up"

    if right_up and not left_up:
        return "right_hand_up"

    # Hug: both wrists crossed to the opposite side of the chest, at
    # roughly chest height (between shoulders and hips).
    if vis(ls, rs_, lw, rw, lhip, rhip):
        chest_top = min(ls.y, rs_.y)
        chest_bottom = max(lhip.y, rhip.y)
        mid_x = (ls.x + rs_.x) / 2.0
        left_wrist_crossed = lw.x > mid_x and chest_top < lw.y < chest_bottom
        right_wrist_crossed = rw.x < mid_x and chest_top < rw.y < chest_bottom
        if left_wrist_crossed and right_wrist_crossed:
            return "hug"

    # Shake hand: right arm extended forward and roughly horizontal at
    # shoulder height, left arm resting down.
    if vis(rs_, re_, rw) and not left_up:
        forward_reach = abs(rw.x - rs_.x)
        vertical_offset = abs(rw.y - rs_.y)
        at_shoulder_height = vertical_offset < RAISE_MARGIN * 1.5
        extended = forward_reach > REACH_MARGIN
        if at_shoulder_height and extended:
            return "shake_hand"

    return "neutral"


def arm_command(action_name: str):
    """Fire one preset G1ArmActionClient animation via the CLI subprocess."""
    if not motion_enabled:
        return False, "Motion is disabled. Click Enable Motion first."

    if not G1_ARM_CLI.exists():
        return False, f"g1_arm_cli.py not found at {G1_ARM_CLI}"

    run_command_async(
        ["python3", G1_ARM_CLI, NETWORK_INTERFACE, action_name],
        label=f"arm_{action_name.replace(' ', '_')}",
        timeout=30,
    )
    return True, f"Arm action sent: {action_name}"


def _post_mimic(path, payload=None):
    """Fire-and-forget POST to the standalone arm_mimic_server_right.py process."""
    try:
        req = urllib.request.Request(
            f"{MIMIC_SERVER_URL}{path}",
            data=_json.dumps(payload or {}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=0.5)
    except Exception as e:
        add_log(f"ArmMimic server unreachable: {e}")


def _vec_angle(v1, v2):
    dot = np.dot(v1, v2)
    n = np.linalg.norm(v1) * np.linalg.norm(v2)
    return 0.0 if n < 1e-6 else float(np.arccos(np.clip(dot / n, -1.0, 1.0)))


def right_arm_targets_from_pose(landmarks):
    """
    Map one frame of MediaPipe Pose landmarks to RIGHT ARM ONLY joint
    angles: shoulder pitch, shoulder roll, elbow. No left arm, no waist,
    no wrist -- those joints are not even present in
    arm_mimic_server_right.py's CONTROLLED_JOINTS, so nothing else can
    move regardless of what's returned here.

    Returns {} if the required right-arm landmarks aren't confidently
    visible this frame -- caller should skip the push rather than send
    a guess.

    NOTE: sign conventions are a best-effort guess and MUST be verified
    empirically (single joint at a time, arm clear of obstacles) before
    trusting them at full range/speed.
    """
    L = mp_pose.PoseLandmark

    def pt(idx):
        lm = landmarks[idx]
        ok = lm.visibility is None or lm.visibility >= LANDMARK_VISIBILITY_MIN
        return np.array([lm.x, -lm.y, lm.z]), ok

    rs_, rs_ok = pt(L.RIGHT_SHOULDER.value)
    re_, re_ok = pt(L.RIGHT_ELBOW.value)
    rw, rw_ok = pt(L.RIGHT_WRIST.value)

    if not (rs_ok and re_ok and rw_ok):
        return {}

    upper = re_ - rs_
    forearm = rw - re_

    roll = max(0.0, np.arctan2(abs(upper[0]), -upper[1]))
    pitch = np.arctan2(-upper[2] * PITCH_Z_DAMPING, -upper[1])
    elbow = max(0.0, np.pi - _vec_angle(upper, forearm))

    return {
        J_R_SHOULDER_ROLL: -1.0 * roll,   # sign convention from earlier right-arm testing; verify
        J_R_SHOULDER_PITCH: pitch,
        J_R_ELBOW: elbow,
        J_R_Wrist_Pitch: -1.0 * roll,
    }


def maybe_send_arm_gesture(gesture: str):
    """Debounce a gesture over several frames and a cooldown, then dispatch it."""
    global _gesture_streak_value, _gesture_streak_count
    global _last_arm_command_time, _last_arm_action_sent

    if gesture == _gesture_streak_value:
        _gesture_streak_count += 1
    else:
        _gesture_streak_value = gesture
        _gesture_streak_count = 1

    if _gesture_streak_count < STREAK_NEEDED:
        return

    now = time.time()
    if now - _last_arm_command_time < ARM_COMMAND_COOLDOWN:
        return

    if gesture == "neutral":
        if _last_arm_action_sent != "neutral":
            ok, message = arm_command("release arm")
            if ok:
                _last_arm_command_time = now
                _last_arm_action_sent = "neutral"
            add_log(f"Pose gesture -> release arm: {message}")
        return

    action_name = GESTURE_TO_ACTION.get(gesture)
    if action_name is None or action_name == _last_arm_action_sent:
        return

    ok, message = arm_command(action_name)
    if ok:
        _last_arm_command_time = now
        _last_arm_action_sent = action_name
    add_log(f"Pose gesture -> {gesture} ({action_name}): {message}")


def draw_pose(frame):
    """If pose detection is enabled, draw the skeleton. Exactly one of two
    mutually-exclusive right-arm control paths runs, never both:
      - mimic_enabled: continuous joint streaming to arm_mimic_server_right.py
      - otherwise: the original gesture-preset classifier/dispatcher
    """
    global _last_mimic_push

    if not pose_detection_enabled:
        return frame
    if not init_pose_detector():
        cv2.putText(frame, "Pose detector unavailable", (20, 104),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        return frame

    try:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        rgb.flags.writeable = False
        with pose_lock:
            results = pose_detector.process(rgb)

        label = "no pose"
        if results.pose_landmarks:
            mp_drawing.draw_landmarks(
                frame,
                results.pose_landmarks,
                mp_pose.POSE_CONNECTIONS,
                landmark_drawing_spec=mp_drawing_styles.get_default_pose_landmarks_style(),
            )

            if mimic_enabled:
                label = "mimic (right arm)"
                now = time.time()
                if now - _last_mimic_push > MIMIC_PUSH_INTERVAL:
                    targets = right_arm_targets_from_pose(results.pose_landmarks.landmark)
                    if targets:
                        _post_mimic("/targets", {"targets": {str(k): v for k, v in targets.items()}})
                    _last_mimic_push = now
            else:
                gesture = classify_body_gesture(results.pose_landmarks.landmark)
                maybe_send_arm_gesture(gesture)
                label = gesture

        cv2.putText(
            frame,
            f"Pose: {label}",
            (20, 104),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 200, 0),
            2,
        )
    except Exception as e:
        add_log(f"Pose detection error: {e}")
        cv2.putText(frame, "Pose detection error", (20, 104),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
    return frame


def run_command_async(cmd, label="command", timeout=120):
    def worker():
        add_log(f"START {label}: {' '.join(str(x) for x in cmd)}")
        try:
            result = subprocess.run(
                [str(x) for x in cmd],
                cwd=str(BASE_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
            )
            output = result.stdout.strip()
            if output:
                for line in output.splitlines()[-80:]:
                    add_log(f"{label}: {line}")
            add_log(f"END {label}, return code={result.returncode}")
        except subprocess.TimeoutExpired:
            add_log(f"TIMEOUT {label}")
        except Exception as e:
            add_log(f"ERROR {label}: {e}")

    threading.Thread(target=worker, daemon=True).start()


def encode_jpg(frame):
    ok, jpg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    if not ok:
        return None
    return jpg.tobytes()


def blank_frame(text, width=960, height=540):
    img = np.zeros((height, width, 3), dtype=np.uint8)
    cv2.putText(
        img,
        text[:90],
        (35, height // 2),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (255, 255, 255),
        2,
    )
    return img


def normalize_frame(frame):
    if frame is None:
        return blank_frame("Empty frame")

    if frame.dtype == np.uint16:
        frame = cv2.convertScaleAbs(frame, alpha=0.03)
        frame = cv2.applyColorMap(frame, cv2.COLORMAP_JET)

    if len(frame.shape) == 2:
        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

    return cv2.resize(frame, (960, 540))


def draw_zones(frame, label):
    h, w = frame.shape[:2]
    x1 = w // 3
    x2 = 2 * w // 3

    cv2.line(frame, (x1, 0), (x1, h), (255, 255, 255), 2)
    cv2.line(frame, (x2, 0), (x2, h), (255, 255, 255), 2)

    cv2.putText(frame, "LEFT", (20, 42), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
    cv2.putText(frame, "CENTER", (x1 + 20, 42), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
    cv2.putText(frame, "RIGHT", (x2 + 20, 42), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)

    cv2.putText(
        frame,
        f"{label} | {datetime.now().strftime('%H:%M:%S')}",
        (20, h - 24),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        (255, 255, 255),
        2,
    )
    return frame


def generate_v4l2_frames():
    last_id = None
    cap = None

    while True:
        with camera_lock:
            current_id = camera_id

        if cap is None or current_id != last_id:
            if cap is not None:
                cap.release()

            add_log(f"Opening V4L2 camera /dev/video{current_id}")
            cap = cv2.VideoCapture(current_id, cv2.CAP_V4L2)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            cap.set(cv2.CAP_PROP_FPS, 15)
            last_id = current_id
            time.sleep(0.4)

        ok, frame = cap.read()

        if not ok or frame is None:
            frame = blank_frame(f"/dev/video{current_id} not available")
            time.sleep(0.2)
        else:
            frame = normalize_frame(frame)
            frame = draw_zones(frame, f"V4L2 /dev/video{current_id}")
            frame = draw_faces(frame)
            frame = draw_pose(frame)

        jpg = encode_jpg(frame)
        if jpg:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"

        time.sleep(0.05)


def generate_realsense_frames(mode="combined"):
    if not HAS_REALSENSE:
        frame = blank_frame("pyrealsense2 is not installed. Use /dev/video mode.")
        jpg = encode_jpg(frame)
        while True:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
            time.sleep(0.3)

    pipeline = rs.pipeline()
    config = rs.config()

    try:
        add_log(f"Starting RealSense stream: {mode}")
        config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
        config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)

        pipeline.start(config)
        align = rs.align(rs.stream.color)

        while True:
            frames = pipeline.wait_for_frames(timeout_ms=5000)
            frames = align.process(frames)

            color_frame = frames.get_color_frame()
            depth_frame = frames.get_depth_frame()

            if not color_frame or not depth_frame:
                frame = blank_frame("No RealSense RGB/depth frame")
            else:
                color = np.asanyarray(color_frame.get_data())
                depth = np.asanyarray(depth_frame.get_data())

                depth_color = cv2.applyColorMap(
                    cv2.convertScaleAbs(depth, alpha=0.03),
                    cv2.COLORMAP_JET,
                )

                color = cv2.resize(color, (960, 540))
                depth_color = cv2.resize(depth_color, (960, 540))

                color = draw_zones(color, "RealSense RGB")
                depth_color = draw_zones(depth_color, "RealSense Depth")
                color = draw_faces(color)
                color = draw_pose(color)

                if mode == "rgb":
                    frame = color
                elif mode == "depth":
                    frame = depth_color
                else:
                    frame = np.hstack((color, depth_color))

            jpg = encode_jpg(frame)
            if jpg:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"

            time.sleep(0.04)

    except Exception as e:
        add_log(f"RealSense error: {e}")
        frame = blank_frame("RealSense error: " + str(e))
        jpg = encode_jpg(frame)
        while True:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
            time.sleep(0.3)

    finally:
        try:
            pipeline.stop()
        except Exception:
            pass


def audio_path(choice: str):
    candidates = [
        AUDIO_DIR / f"phrase{choice}.wav",
        AUDIO_DIR / f"mjhome_phrase{choice}.wav",
        BASE_DIR / f"phrase{choice}.wav",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def say_phrase(choice: str):
    """
    Use the old MJHome speaker script first, because it is confirmed to work
    on this G1. Local WAV/aplay is kept only as fallback.
    """
    if choice not in {"1", "2", "3", "4", "random"}:
        return False, f"Unknown voice choice: {choice}"

    # First choice: use old working MJHome speaker channel.
    # This does NOT modify the old web page; it only calls the old script.
    if MJHOME_SAY_SCRIPT.exists():
        run_command_async(
            ["python3", MJHOME_SAY_SCRIPT, NETWORK_INTERFACE, choice],
            label=f"say_oldspeaker_{choice}",
            timeout=120,
        )
        return True, f"Voice {choice} started through old speaker script."

    # Fallback: local WAV through ALSA/aplay.
    if choice == "random":
        choice = "1"

    wav = audio_path(choice)
    if wav is not None:
        cmd = ["aplay"]
        if AUDIO_DEVICE:
            cmd += ["-D", AUDIO_DEVICE]
        cmd.append(str(wav))
        run_command_async(cmd, label=f"say_phrase_{choice}", timeout=60)
        return True, f"Playing phrase {choice}: {PHRASES[choice]}"

    return False, f"Missing mjhome_say.py and missing audio/phrase{choice}.wav."

def say_text(text: str = ""):
    if not G1_TTS_CLI.exists():
        return False, f"mjhome_tts.py not found at {G1_TTS_CLI}"

    cmd = ["python3", G1_TTS_CLI, NETWORK_INTERFACE]
    if text:
        cmd.append(text)

    run_command_async(cmd, label="tts_hello", timeout=30)
    return True, f"TTS started: {text or 'default greeting'}"


def move_robot_command(cmd: str):
    global motion_enabled

    allowed = {"stop", "forward", "back", "left", "right", "turnleft", "turnright"}
    if cmd not in allowed:
        return False, f"Unknown movement command: {cmd}"

    if cmd != "stop" and not motion_enabled:
        return False, "Motion is disabled. Click Enable Motion first."

    if not G1_WALK_CLI.exists():
        return False, f"g1_walk_cli.py not found at {G1_WALK_CLI}"

    run_command_async(
        ["python3", G1_WALK_CLI, NETWORK_INTERFACE, cmd],
        label=f"move_{cmd}",
        timeout=120,
    )
    return True, f"Movement command sent: {cmd}"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/video_feed")
def video_feed():
    with camera_lock:
        mode = camera_mode

    if mode == "rs_combined":
        return Response(generate_realsense_frames("combined"), mimetype="multipart/x-mixed-replace; boundary=frame")
    if mode == "rs_rgb":
        return Response(generate_realsense_frames("rgb"), mimetype="multipart/x-mixed-replace; boundary=frame")
    if mode == "rs_depth":
        return Response(generate_realsense_frames("depth"), mimetype="multipart/x-mixed-replace; boundary=frame")

    return Response(generate_v4l2_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/api/set_camera", methods=["POST"])
def set_camera():
    global camera_mode, camera_id

    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "v4l2")

    with camera_lock:
        if mode == "v4l2":
            try:
                camera_id = int(data.get("camera_id", DEFAULT_CAMERA_ID))
            except Exception:
                return jsonify({"ok": False, "message": f"Invalid camera id: {data.get('camera_id')}"})
            camera_mode = "v4l2"
            add_log(f"Camera switched to /dev/video{camera_id}")
            return jsonify({"ok": True, "message": f"Camera switched to /dev/video{camera_id}"})

        if mode in {"rs_combined", "rs_rgb", "rs_depth"}:
            camera_mode = mode
            add_log(f"Camera switched to {mode}")
            if not HAS_REALSENSE:
                return jsonify({"ok": True, "message": f"{mode} selected, but pyrealsense2 is not installed."})
            return jsonify({"ok": True, "message": f"Camera switched to {mode}"})

    return jsonify({"ok": False, "message": f"Unknown camera mode: {mode}"})


@app.route("/api/set_face_detection", methods=["POST"])
def api_set_face_detection():
    global face_detection_enabled

    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled", True))
    face_detection_enabled = enabled

    if enabled:
        loaded = init_haar_cascade()
        if not loaded:
            face_detection_enabled = False
            return jsonify({
                "ok": False,
                "message": "Haar cascade file not found. Place haarcascade_frontalface_default.xml "
                            "next to app.py, or set HAAR_CASCADE_PATH.",
            })

    add_log(f"Face detection enabled: {face_detection_enabled}")
    return jsonify({"ok": True, "message": f"Face detection enabled: {face_detection_enabled}"})


@app.route("/api/set_pose_detection", methods=["POST"])
def api_set_pose_detection():
    global pose_detection_enabled, _gesture_streak_value, _gesture_streak_count
    global _last_arm_action_sent, mimic_enabled

    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled", True))
    pose_detection_enabled = enabled

    if enabled:
        loaded = init_pose_detector()
        if not loaded:
            pose_detection_enabled = False
            return jsonify({"ok": False, "message": "MediaPipe Pose could not be initialized."})
    else:
        # Reset debounce state so re-enabling starts clean, and release the
        # arm if it was mid-gesture.
        if _last_arm_action_sent != "neutral":
            arm_command("release arm")
        _gesture_streak_value = "neutral"
        _gesture_streak_count = 0
        _last_arm_action_sent = "neutral"
        if mimic_enabled:
            mimic_enabled = False
            _post_mimic("/disable")

    add_log(f"Pose detection enabled: {pose_detection_enabled}")
    return jsonify({"ok": True, "message": f"Pose detection enabled: {pose_detection_enabled}"})


@app.route("/api/set_mimic", methods=["POST"])
def api_set_mimic():
    """Toggle continuous right-arm mimicry. Mutually exclusive with the
    gesture-preset system: enabling this releases any in-progress preset
    action so the two channels never fight over the right arm."""
    global mimic_enabled, pose_detection_enabled, _last_arm_action_sent

    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled", True))

    if enabled and not pose_detection_enabled:
        loaded = init_pose_detector()
        if not loaded:
            return jsonify({"ok": False, "message": "MediaPipe Pose could not be initialized."})
        pose_detection_enabled = True

    if enabled and _last_arm_action_sent != "neutral":
        arm_command("release arm")
        _last_arm_action_sent = "neutral"

    mimic_enabled = enabled
    _post_mimic("/enable" if enabled else "/disable")

    add_log(f"Right-arm mimic enabled: {mimic_enabled}")
    return jsonify({"ok": True, "message": f"Right-arm mimic enabled: {mimic_enabled}"})


@app.route("/api/say/<choice>", methods=["POST"])
def api_say(choice):
    ok, message = say_phrase(choice)
    return jsonify({"ok": ok, "message": message})

@app.route("/api/hello", methods=["POST"])
def api_hello():
    ok, message = say_phrase("4")
    return jsonify({"ok": ok, "message": message})


@app.route("/api/motion_enable", methods=["POST"])
def api_motion_enable():
    global motion_enabled

    data = request.get_json(silent=True) or {}
    motion_enabled = bool(data.get("enabled", True))
    add_log(f"Motion enabled: {motion_enabled}")
    return jsonify({"ok": True, "message": f"Motion enabled: {motion_enabled}"})


@app.route("/api/move/<cmd>", methods=["POST"])
def api_move(cmd):
    ok, message = move_robot_command(cmd)
    return jsonify({"ok": ok, "message": message})


@app.route("/api/arm/<gesture>", methods=["POST"])
def api_arm(gesture):
    if gesture not in ARM_ACTION_NAMES:
        return jsonify({"ok": False, "message": f"Unknown gesture: {gesture}"})
    if mimic_enabled:
        return jsonify({"ok": False, "message": "Disable right-arm mimic first -- it and preset actions can't run together."})
    ok, message = arm_command(gesture)
    return jsonify({"ok": ok, "message": message})


@app.route("/api/demo/<zone>", methods=["POST"])
def api_demo(zone):
    if zone == "center":
        ok, message = say_phrase("1")
        return jsonify({"ok": ok, "message": "Center Welcome: " + message})

    if zone == "right":
        ok1, msg1 = say_phrase("2")
        time.sleep(0.35)
        ok2, msg2 = move_robot_command("right")
        return jsonify({"ok": ok1 and ok2, "message": f"Right Welcome: {msg1}; {msg2}"})

    if zone == "left":
        ok1, msg1 = say_phrase("3")
        time.sleep(0.35)
        ok2, msg2 = move_robot_command("left")
        return jsonify({"ok": ok1 and ok2, "message": f"Left Welcome: {msg1}; {msg2}"})

    return jsonify({"ok": False, "message": f"Unknown demo zone: {zone}"})


@app.route("/api/status", methods=["GET"])
def api_status():
    with camera_lock:
        cam = f"{camera_mode}, /dev/video{camera_id}"
    msg = (
        f"Camera: {cam}\n"
        f"pyrealsense2: {HAS_REALSENSE}\n"
        f"Motion enabled: {motion_enabled}\n"
        f"Face detection enabled: {face_detection_enabled}\n"
        f"Haar cascade loaded: {haar_face_cascade is not None}\n"
        f"Pose detection enabled: {pose_detection_enabled}\n"
        f"Pose detector loaded: {pose_detector is not None}\n"
        f"Right-arm mimic enabled: {mimic_enabled}\n"
        f"Last arm action sent: {_last_arm_action_sent}\n"
        f"Network interface: {NETWORK_INTERFACE}\n"
        f"G1 walk script: {G1_WALK_CLI}\n"
        f"G1 arm script: {G1_ARM_CLI}\n"
        f"Audio device: {AUDIO_DEVICE or 'default'}"
    )
    return jsonify({"ok": True, "message": msg})


@app.route("/api/log", methods=["GET"])
def api_log():
    return jsonify({"ok": True, "log": log_lines})


if __name__ == "__main__":
    add_log("MJHome G1 Welcome Project starting...")
    add_log(f"BASE_DIR={BASE_DIR}")
    add_log(f"PORT={PORT}")
    add_log(f"HAS_REALSENSE={HAS_REALSENSE}")
    add_log(f"G1_WALK_CLI={G1_WALK_CLI}")
    add_log(f"G1_ARM_CLI={G1_ARM_CLI}")
    if face_detection_enabled:
        init_haar_cascade()
    if pose_detection_enabled:
        init_pose_detector()
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
