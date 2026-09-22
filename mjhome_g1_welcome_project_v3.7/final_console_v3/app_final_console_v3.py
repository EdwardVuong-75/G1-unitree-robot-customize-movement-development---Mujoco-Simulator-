import atexit
import json
import os
import signal
import subprocess
import threading
import time
import urllib.request
import urllib.error
import wave
import audioop
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from flask import Flask, Response, jsonify, render_template, request

try:
    import mediapipe as mp
    HAS_MEDIAPIPE = True
except Exception:
    mp = None
    HAS_MEDIAPIPE = False

BASE_DIR = Path(__file__).resolve().parent
TMP_DIR = BASE_DIR / "tmp"
TMP_DIR.mkdir(exist_ok=True)
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

PORT = int(os.environ.get("PORT", "5014"))
LAPTOP_AGENT_URL = os.environ.get("LAPTOP_AGENT_URL", "http://192.168.123.100:5020")
OLD_CAMERA_BASE = os.environ.get("OLD_CAMERA_BASE", "http://127.0.0.1:5010")
OLD_CAMERA_AUTOSTART = os.environ.get("OLD_CAMERA_AUTOSTART", "1") == "1"
OLD_CAMERA_COMMAND = os.environ.get(
    "OLD_CAMERA_COMMAND",
    "cd /home/unitree/mjhome_g1_welcome_project && PORT=5010 ./start.sh"
)
NETWORK_INTERFACE = os.environ.get("NETWORK_INTERFACE", "eth0")
BOYA_MIC_DEVICE = os.environ.get("BOYA_MIC_DEVICE", "plughw:2,0")
LISTEN_SECONDS = float(os.environ.get("LISTEN_SECONDS", "3.2"))
TTS_GAIN_DB = os.environ.get("TTS_GAIN_DB", "10")

HAAR_CASCADE_PATH = os.environ.get("HAAR_CASCADE_PATH", "")

G1_WALK_CLI = Path(os.environ.get("G1_WALK_CLI", "/home/unitree/unitree_sdk2_python/g1_walk_cli.py"))
MJHOME_SAY_SCRIPT = Path(os.environ.get("MJHOME_SAY_SCRIPT", "/home/unitree/unitree_sdk2_python/mjhome_say.py"))
G1_GESTURE_CLI = Path(os.environ.get("G1_GESTURE_CLI", str(BASE_DIR / "g1_gesture_cli.py")))
UNITREE_AUDIO_DIR = Path(os.environ.get("UNITREE_AUDIO_DIR", "/home/unitree/unitree_sdk2_python"))

WELCOME_TEXTS = {
    "1": "Welcome to MJ Home, your cozy and future-ready space.",
    "2": "We are MJ Home, building a sustainable, long-term future for everyone.",
    "3": "MJ Home is proud to serve our community and help transform New Zealand.",
}

app = Flask(__name__)

log_lines = []
MAX_LOG_LINES = 1000

motion_enabled = False
listening = False
listen_thread = None
listen_stop_event = threading.Event()
speak_lock = threading.Lock()

last_result = None
last_stt_text = ""
last_user_text = ""
last_wav_path = ""
vision_mode = "off"

camera_process = None
camera_process_owned = False

mp_face_mesh = None
mp_pose = None
mp_drawing = None
mp_face = None
mp_pose_module = None
mp_frame_counter = 0

haar_face_cascade = None


def add_log(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    log_lines.append(line)
    if len(log_lines) > MAX_LOG_LINES:
        del log_lines[: len(log_lines) - MAX_LOG_LINES]


def is_url_ok(url, timeout=2):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status < 500
    except Exception:
        return False


def post_json(url, payload, timeout=120):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ensure_old_camera_service():
    global camera_process, camera_process_owned
    status_url = OLD_CAMERA_BASE.rstrip("/") + "/api/status"
    if is_url_ok(status_url, timeout=2):
        add_log(f"Old camera service already running: {OLD_CAMERA_BASE}")
        return True

    if not OLD_CAMERA_AUTOSTART:
        add_log("Old camera service not running and autostart disabled.")
        return False

    add_log(f"Starting old camera service: {OLD_CAMERA_COMMAND}")
    logfile = open(LOG_DIR / "old_camera_service.log", "a")
    camera_process = subprocess.Popen(
        ["bash", "-lc", OLD_CAMERA_COMMAND],
        stdout=logfile,
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
        text=True
    )
    camera_process_owned = True

    for _ in range(20):
        time.sleep(0.5)
        if is_url_ok(status_url, timeout=2):
            add_log("Old camera service started successfully.")
            return True

    add_log("Old camera service did not become ready in time.")
    return False


def cleanup_child_processes():
    global camera_process, camera_process_owned
    if camera_process is not None and camera_process_owned:
        try:
            os.killpg(os.getpgid(camera_process.pid), signal.SIGTERM)
        except Exception:
            pass


atexit.register(cleanup_child_processes)


def switch_old_camera_to_combined():
    # Try both JSON and query styles for compatibility with old versions.
    endpoints = [
        ("/api/set_camera", {"mode": "rs_combined"}),
        ("/api/set_camera?mode=rs_combined", {}),
    ]
    for path, payload in endpoints:
        try:
            url = OLD_CAMERA_BASE.rstrip("/") + path
            if payload:
                post_json(url, payload, timeout=5)
            else:
                req = urllib.request.Request(url, data=b"", method="POST")
                urllib.request.urlopen(req, timeout=5).read()
            add_log(f"Requested old camera combined mode via {path}")
            return True
        except Exception as e:
            add_log(f"Old camera mode request failed via {path}: {e}")
    return False


def run_subprocess(cmd, label="cmd", timeout=120):
    add_log(f"START {label}: {' '.join(str(x) for x in cmd)}")
    r = subprocess.run([str(x) for x in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
    out = r.stdout.strip()
    if out:
        for line in out.splitlines()[-100:]:
            add_log(f"{label}: {line}")
    add_log(f"END {label}, return code={r.returncode}")
    return r.returncode == 0, out


def run_subprocess_async(cmd, label="cmd", timeout=120):
    threading.Thread(target=lambda: run_subprocess(cmd, label, timeout), daemon=True).start()


def analyze_wav(path):
    try:
        wf = wave.open(str(path), "rb")
        data = wf.readframes(wf.getnframes())
        wf.close()
        return {"size": os.path.getsize(path), "rms": audioop.rms(data, 2) if data else 0,
                "peak": audioop.max(data, 2) if data else 0, "path": str(path)}
    except Exception as e:
        return {"size": 0, "rms": 0, "peak": 0, "path": str(path), "error": str(e)}


def record_boya(seconds=None):
    seconds = float(seconds if seconds is not None else LISTEN_SECONDS)
    seconds = max(1.5, min(seconds, 8.0))
    raw_path = TMP_DIR / "listen_raw_48k.wav"
    wav_path = TMP_DIR / "listen_processed_16k.wav"

    cmd_record = [
        "arecord", "-D", BOYA_MIC_DEVICE,
        "-f", "S16_LE", "-r", "48000", "-c", "2",
        "-d", str(round(seconds, 2)), "-q", str(raw_path)
    ]
    add_log(f"Recording BOYA {seconds:.1f}s from {BOYA_MIC_DEVICE}")
    subprocess.run(cmd_record, check=True)

    raw_stats = analyze_wav(raw_path)
    add_log(f"Raw audio stats: rms={raw_stats['rms']}, peak={raw_stats['peak']}, size={raw_stats['size']}")

    filter_chain = "highpass=f=120,lowpass=f=7000,dynaudnorm=f=150:g=5:p=0.70,volume=3dB,alimiter=limit=0.95"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(raw_path),
        "-ac", "1", "-ar", "16000", "-af", filter_chain, str(wav_path)
    ], check=True)

    proc_stats = analyze_wav(wav_path)
    add_log(f"Processed audio stats: rms={proc_stats['rms']}, peak={proc_stats['peak']}, size={proc_stats['size']}")
    return wav_path, raw_stats, proc_stats


def upload_audio_to_laptop(wav_path):
    url = LAPTOP_AGENT_URL.rstrip("/") + "/api/audio_chat"
    cmd = ["curl", "-s", "-X", "POST", "-F", f"audio=@{wav_path}", url]
    add_log(f"Uploading audio to laptop: {url}")
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError(r.stdout)
    return json.loads(r.stdout)


def laptop_chat_text(text):
    return post_json(LAPTOP_AGENT_URL.rstrip("/") + "/api/chat_text", {"text": text}, timeout=120)


def append_result(user_text, stt_text, result):
    global last_result, last_stt_text, last_user_text
    last_user_text = user_text or ""
    last_stt_text = stt_text or ""
    last_result = result
    add_log(f"USER/STT: {last_stt_text or last_user_text}")
    if result:
        add_log(f"REPLY: {result.get('reply')}")
        add_log(f"ACTION: {result.get('action')} | MOTION: {result.get('motion')} | SOURCE: {result.get('source')}")


def speak_dynamic_text_sync(text):
    if not text:
        return False, "Empty dynamic speech text."
    if not MJHOME_SAY_SCRIPT.exists():
        return False, f"mjhome_say.py not found: {MJHOME_SAY_SCRIPT}"

    with speak_lock:
        try:
            payload_path = TMP_DIR / "tts_payload.json"
            laptop_wav = TMP_DIR / "reply_from_laptop.wav"
            target_wav = UNITREE_AUDIO_DIR / "greeting_1_16k.wav"
            backup_wav = UNITREE_AUDIO_DIR / "greeting_1_16k.wav.original_backup"

            payload_path.write_text(json.dumps({"text": text}, ensure_ascii=False), encoding="utf-8")
            url = LAPTOP_AGENT_URL.rstrip("/") + "/api/tts_wav"
            add_log(f"Requesting laptop TTS: {url}")
            subprocess.run([
                "curl", "-s", "-X", "POST", "-H", "Content-Type: application/json",
                "--data-binary", f"@{payload_path}", "-o", str(laptop_wav), url
            ], check=True)

            if not laptop_wav.exists() or laptop_wav.stat().st_size < 1000:
                raise RuntimeError("Laptop TTS wav missing or too small.")

            if target_wav.exists() and not backup_wav.exists():
                subprocess.run(["cp", str(target_wav), str(backup_wav)], check=True)
                add_log(f"Backed up original greeting: {backup_wav}")

            tts_filter = f"loudnorm=I=-12:TP=-1.0:LRA=7,volume={TTS_GAIN_DB}dB,alimiter=limit=0.95"
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error", "-i", str(laptop_wav),
                "-ac", "1", "-ar", "16000", "-af", tts_filter, str(target_wav)
            ], check=True)

            add_log(f"Speaking dynamic text: {text}")
            run_subprocess(["python3", MJHOME_SAY_SCRIPT, NETWORK_INTERFACE, "1"],
                           label="speak_dynamic", timeout=120)

            if backup_wav.exists():
                subprocess.run(["cp", str(backup_wav), str(target_wav)], check=True)
                add_log("Restored original greeting_1_16k.wav after dynamic speech.")

            return True, "Dynamic speech finished."
        except Exception as e:
            add_log(f"Dynamic TTS failed: {e}")
            return False, str(e)


def execute_gesture(name):
    normalized = str(name).strip().lower().replace("-", "_").replace(" ", "_")
    alias = {
        "handshake": "shake_hand",
        "shakehand": "shake_hand",
        "shake_hand": "shake_hand",
        "facewave": "face_wave",
        "face_wave": "face_wave",
        "wave": "face_wave",
    }
    action = alias.get(normalized, normalized)

    if action not in {"shake_hand", "face_wave", "release_arm"}:
        return False, f"Unknown gesture: {name}"

    if not G1_GESTURE_CLI.exists():
        return False, f"Gesture script not found: {G1_GESTURE_CLI}"

    run_subprocess_async(["python3", G1_GESTURE_CLI, NETWORK_INTERFACE, action],
                         label=f"gesture_{action}", timeout=40)
    return True, f"Gesture command sent: {action}"


def speak_welcome(choice):
    text = WELCOME_TEXTS.get(str(choice), WELCOME_TEXTS["1"])
    threading.Thread(target=lambda: speak_dynamic_text_sync(text), daemon=True).start()
    if str(choice) == "1":
        execute_gesture("shake_hand")
    return True, f"Welcome {choice} started."


def auto_gesture_for_result(result):
    if not result:
        return
    intent = str(result.get("intent", "")).lower()
    reply = str(result.get("reply", "")).lower()

    is_greeting = (
        intent in {"general_greeting", "morning_greeting", "evening_greeting"}
        or ("welcome" in reply and "thank you for visiting" not in reply)
    )
    is_exit = (
        intent in {"exit", "goodbye", "farewell"}
        or "thank you for visiting" in reply
        or "have a great day" in reply
        or "goodbye" in reply
        or "see you" in reply
    )
    if is_exit:
        execute_gesture("face_wave")
    elif is_greeting:
        execute_gesture("shake_hand")


def speak_result_sync(result):
    if not result:
        return False, "No result to speak."
    reply = str(result.get("reply", "")).strip()
    ok, msg = speak_dynamic_text_sync(reply)
    auto_gesture_for_result(result)
    return ok, msg


def execute_motion(cmd):
    global motion_enabled
    allowed = {"stop", "forward", "back", "left", "right", "turnleft", "turnright"}
    if cmd not in allowed:
        return False, f"Unknown motion command: {cmd}"
    if cmd != "stop" and not motion_enabled:
        return False, "Motion is disabled. Click Enable Motion first."
    if not G1_WALK_CLI.exists():
        return False, f"g1_walk_cli.py not found: {G1_WALK_CLI}"
    run_subprocess_async(["python3", G1_WALK_CLI, NETWORK_INTERFACE, cmd],
                         label=f"move_{cmd}", timeout=120)
    return True, f"Motion command sent: {cmd}"


def execute_result_action(result):
    if not result:
        return False, "No safe action to execute."
    action = str(result.get("action", "none"))
    motion = str(result.get("motion", "none"))
    if action in {"none", "say", "call_staff"}:
        return True, "No robot motion required."
    if action == "stop":
        return execute_motion("stop")
    if action == "guide_to_section":
        if motion == "none":
            return True, "Guide action suggested, but no motion configured."
        return execute_motion(motion)
    if action in {"forward", "back", "left", "right", "turnleft", "turnright"}:
        return execute_motion(action)
    return False, f"Unsupported action: {action}"


def listen_loop():
    global listening
    add_log("Continuous listening loop started.")
    while not listen_stop_event.is_set():
        try:
            if speak_lock.locked():
                time.sleep(0.2)
                continue
            wav_path, raw_stats, proc_stats = record_boya(LISTEN_SECONDS)
            if proc_stats.get("rms", 0) < 30:
                add_log("Audio too quiet; ignoring this segment.")
                continue
            response = upload_audio_to_laptop(wav_path)
            stt = (response.get("stt_text") or "").strip()
            result = response.get("result")
            if not stt:
                add_log("No speech recognized in this segment.")
                continue
            append_result("", stt, result)
            if result:
                speak_result_sync(result)
        except Exception as e:
            add_log(f"LISTEN ERROR: {e}")
            time.sleep(0.5)
    listening = False
    add_log("Continuous listening loop stopped.")


def start_listening():
    global listening, listen_thread
    if listening:
        return True, "Already listening."
    listen_stop_event.clear()
    listening = True
    listen_thread = threading.Thread(target=listen_loop, daemon=True)
    listen_thread.start()
    return True, "Listening started."


def stop_listening():
    global listening
    listen_stop_event.set()
    listening = False
    return True, "Listening stopped."


def init_mediapipe():
    global mp_face_mesh, mp_pose, mp_drawing, mp_face, mp_pose_module
    if not HAS_MEDIAPIPE:
        return False
    if mp_face_mesh is not None and mp_pose is not None:
        return True
    mp_drawing = mp.solutions.drawing_utils
    mp_face = mp.solutions.face_mesh
    mp_pose_module = mp.solutions.pose
    mp_face_mesh = mp_face.FaceMesh(
        static_image_mode=False,
        max_num_faces=2,
        refine_landmarks=False,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5
    )
    mp_pose = mp_pose_module.Pose(
        static_image_mode=False,
        model_complexity=0,
        smooth_landmarks=True,
        enable_segmentation=False,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5
    )
    add_log("MediaPipe initialized.")
    return True


def init_haar_cascade():
    global haar_face_cascade
    if haar_face_cascade is not None:
        return True

    # Prefer an explicit path (e.g. a copy of haarcascade_frontalface_default.xml
    # placed alongside this script or set via HAAR_CASCADE_PATH). Fall back to
    # the copy that ships inside the installed opencv-python package.
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
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(40, 40)
    )
    return faces


def apply_vision_to_left_rgb(frame):
    global mp_frame_counter
    if vision_mode == "off":
        return frame

    if vision_mode == "haar":
        h, w = frame.shape[:2]
        if w >= int(h * 1.7):
            mid = w // 2
            left = frame[:, :mid].copy()
            right = frame[:, mid:].copy()
        else:
            mid = None
            left = frame.copy()
            right = None

        try:
            faces = detect_faces_haar(left)
            for (x, y, fw, fh) in faces:
                cv2.rectangle(left, (x, y), (x + fw, y + fh), (0, 255, 0), 2)
            cv2.putText(
                left,
                f"Vision: haar | faces={len(faces)}",
                (20, 76),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.58,
                (255, 255, 255),
                2
            )
        except Exception as e:
            add_log(f"Haar cascade error: {e}")
            cv2.putText(left, "Haar cascade error", (20, 76),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

        if right is not None:
            return np.hstack([left, right])
        return left

    if not HAS_MEDIAPIPE:
        cv2.putText(frame, "MediaPipe not installed", (20, 76),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        return frame

    init_mediapipe()

    h, w = frame.shape[:2]
    if w >= int(h * 1.7):
        mid = w // 2
        left = frame[:, :mid].copy()
        right = frame[:, mid:].copy()
    else:
        mid = None
        left = frame.copy()
        right = None

    mp_frame_counter += 1
    # Process every 4th frame to prevent freezing. Keep the stream smooth.
    if mp_frame_counter % 4 != 0:
        if right is not None:
            return np.hstack([left, right])
        return left

    try:
        rgb = cv2.cvtColor(np.ascontiguousarray(left), cv2.COLOR_BGR2RGB)
        rgb.flags.writeable = False

        face_count = 0
        pose_found = False

        if vision_mode in {"face", "both"}:
            fr = mp_face_mesh.process(rgb)
            if fr.multi_face_landmarks:
                face_count = len(fr.multi_face_landmarks)
                for face_landmarks in fr.multi_face_landmarks:
                    mp_drawing.draw_landmarks(
                        left,
                        face_landmarks,
                        mp_face.FACEMESH_CONTOURS,
                        landmark_drawing_spec=None,
                        connection_drawing_spec=mp_drawing.DrawingSpec(thickness=1, circle_radius=1)
                    )

        if vision_mode in {"pose", "both"}:
            pr = mp_pose.process(rgb)
            if pr.pose_landmarks:
                pose_found = True
                mp_drawing.draw_landmarks(
                    left,
                    pr.pose_landmarks,
                    mp_pose_module.POSE_CONNECTIONS,
                    landmark_drawing_spec=mp_drawing.DrawingSpec(thickness=2, circle_radius=3),
                    connection_drawing_spec=mp_drawing.DrawingSpec(thickness=2)
                )

        cv2.putText(
            left,
            f"Vision: {vision_mode} | faces={face_count} | pose={'yes' if pose_found else 'no'}",
            (20, 76),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.58,
            (255, 255, 255),
            2
        )

    except Exception as e:
        add_log(f"MediaPipe error: {e}")
        cv2.putText(left, "MediaPipe error", (20, 76),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

    if right is not None:
        return np.hstack([left, right])
    return left


def encode_jpg(frame):
    ok, jpg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    return jpg.tobytes() if ok else None


def camera_blank(text, width=1280, height=480):
    img = np.zeros((height, width, 3), dtype=np.uint8)
    cv2.putText(img, text[:100], (35, height // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (220, 220, 220), 2)
    return img


def gen_combined_camera():
    ensure_old_camera_service()
    switch_old_camera_to_combined()

    candidates = [
        OLD_CAMERA_BASE.rstrip("/") + "/video_feed",
        OLD_CAMERA_BASE.rstrip("/") + "/video/realsense/combined",
    ]

    while True:
        for url in candidates:
            add_log(f"Opening combined camera stream: {url}")
            cap = cv2.VideoCapture(url)

            if not cap.isOpened():
                add_log(f"Could not open combined camera stream: {url}")
                try:
                    cap.release()
                except Exception:
                    pass
                continue

            add_log(f"Combined camera stream opened: {url}")

            try:
                while True:
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        add_log(f"Combined stream ended: {url}")
                        break

                    if len(frame.shape) == 2:
                        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

                    frame = apply_vision_to_left_rgb(frame)

                    # Do not draw old LEFT/CENTER/RIGHT division lines.
                    h, w = frame.shape[:2]
                    if w < 1000:
                        frame = cv2.resize(frame, (1280, 480))
                    else:
                        frame = cv2.resize(frame, (1280, 480))

                    cv2.putText(frame, "RGB", (20, 32),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255,255,255), 2)
                    cv2.putText(frame, "Depth", (frame.shape[1]//2 + 20, 32),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255,255,255), 2)

                    jpg = encode_jpg(frame)
                    if jpg:
                        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"

                    time.sleep(0.05)

            finally:
                try:
                    cap.release()
                except Exception:
                    pass

        img = camera_blank("Combined camera unavailable. Check old camera service or reload.")
        jpg = encode_jpg(img)
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
        time.sleep(1.0)


def run_sequence_worker(commands):
    global motion_enabled
    add_log(f"Sequence started with {len(commands)} commands.")
    for i, cmd in enumerate(commands, start=1):
        ctype = cmd.get("type")
        add_log(f"Sequence step {i}: {cmd}")
        try:
            if ctype == "say_welcome":
                choice = str(cmd.get("choice", "1"))
                speak_welcome(choice)
                time.sleep(float(cmd.get("after", 0.5)))
            elif ctype == "say_text":
                text = str(cmd.get("text", "")).strip()
                if text:
                    speak_dynamic_text_sync(text)
                time.sleep(float(cmd.get("after", 0.2)))
            elif ctype == "gesture":
                gesture = str(cmd.get("gesture", "shake_hand"))
                execute_gesture(gesture)
                time.sleep(float(cmd.get("after", 0.5)))
            elif ctype == "move":
                move = str(cmd.get("move", "stop"))
                execute_motion(move)
                time.sleep(float(cmd.get("duration", 1.0)))
            elif ctype == "wait":
                time.sleep(float(cmd.get("seconds", 1.0)))
            elif ctype == "enable_motion":
                motion_enabled = True
                add_log("Sequence: motion enabled.")
            elif ctype == "disable_motion":
                motion_enabled = False
                add_log("Sequence: motion disabled.")
            elif ctype == "stop":
                execute_motion("stop")
                time.sleep(0.2)
            else:
                add_log(f"Unknown sequence command type: {ctype}")
        except Exception as e:
            add_log(f"Sequence step error: {e}")
    add_log("Sequence finished.")


@app.route("/")
def index():
    return render_template("final_console_v3.html")


@app.route("/video_feed")
def video_feed():
    return Response(gen_combined_camera(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/api/set_vision", methods=["POST"])
def api_set_vision():
    global vision_mode
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "off")
    if mode not in {"off", "face", "pose", "both", "haar"}:
        return jsonify({"ok": False, "message": "Invalid vision mode."})
    vision_mode = mode
    add_log(f"Vision mode set to {mode}")
    return jsonify({"ok": True, "message": f"Vision mode: {mode}"})


@app.route("/api/chat_text", methods=["POST"])
def api_chat_text():
    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()
    if not text:
        return jsonify({"ok": False, "message": "Empty text."})
    try:
        response = laptop_chat_text(text)
        result = response.get("result")
        append_result(text, "", result)
        return jsonify({"ok": response.get("ok", True), "result": result, "message": response.get("message", "")})
    except Exception as e:
        add_log(f"Chat text error: {e}")
        return jsonify({"ok": False, "message": str(e)})


@app.route("/api/listen_toggle", methods=["POST"])
def api_listen_toggle():
    if listening:
        ok, msg = stop_listening()
    else:
        ok, msg = start_listening()
    return jsonify({"ok": ok, "message": msg, "listening": listening})


@app.route("/api/listen_start", methods=["POST"])
def api_listen_start():
    ok, msg = start_listening()
    return jsonify({"ok": ok, "message": msg, "listening": listening})


@app.route("/api/listen_stop", methods=["POST"])
def api_listen_stop():
    ok, msg = stop_listening()
    return jsonify({"ok": ok, "message": msg, "listening": listening})


@app.route("/api/say_reply", methods=["POST"])
def api_say_reply():
    threading.Thread(target=lambda: speak_result_sync(last_result), daemon=True).start()
    return jsonify({"ok": True, "message": "Speaking reply."})


@app.route("/api/say_text", methods=["POST"])
def api_say_text():
    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()
    threading.Thread(target=lambda: speak_dynamic_text_sync(text), daemon=True).start()
    return jsonify({"ok": True, "message": "Speaking custom text."})


@app.route("/api/say_welcome/<choice>", methods=["POST"])
def api_say_welcome(choice):
    ok, msg = speak_welcome(choice)
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/gesture/<name>", methods=["POST"])
def api_gesture(name):
    ok, msg = execute_gesture(name)
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/execute_action", methods=["POST"])
def api_execute_action():
    ok, msg = execute_result_action(last_result)
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/motion_enable", methods=["POST"])
def api_motion_enable():
    global motion_enabled
    data = request.get_json(silent=True) or {}
    motion_enabled = bool(data.get("enabled", True))
    add_log(f"Motion enabled: {motion_enabled}")
    return jsonify({"ok": True, "message": f"Motion enabled: {motion_enabled}"})


@app.route("/api/move/<cmd>", methods=["POST"])
def api_move(cmd):
    ok, msg = execute_motion(cmd)
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/run_sequence", methods=["POST"])
def api_run_sequence():
    data = request.get_json(silent=True) or {}
    commands = data.get("commands", [])
    if not isinstance(commands, list):
        return jsonify({"ok": False, "message": "commands must be a list."})
    threading.Thread(target=lambda: run_sequence_worker(commands), daemon=True).start()
    return jsonify({"ok": True, "message": f"Sequence started with {len(commands)} commands."})


@app.route("/api/status")
def api_status():
    return jsonify({"ok": True, "message": (
        f"Listening: {listening}\n"
        f"Vision: {vision_mode}\n"
        f"Motion enabled: {motion_enabled}\n"
        f"BOYA mic: {BOYA_MIC_DEVICE}\n"
        f"Laptop agent: {LAPTOP_AGENT_URL}\n"
        f"Old camera service: {OLD_CAMERA_BASE}\n"
        f"MediaPipe: {HAS_MEDIAPIPE}\n"
        f"Last STT: {last_stt_text}\n"
        f"Last reply: {(last_result or {}).get('reply', '')}"
    )})


@app.route("/api/log")
def api_log():
    return jsonify({"ok": True, "log": log_lines})


@app.route("/api/last_result")
def api_last_result():
    return jsonify({"ok": True, "result": last_result, "stt": last_stt_text, "user_text": last_user_text})


if __name__ == "__main__":
    add_log("Final Console V3 starting.")
    add_log(f"PORT={PORT}")
    add_log(f"BOYA_MIC_DEVICE={BOYA_MIC_DEVICE}")
    add_log(f"LAPTOP_AGENT_URL={LAPTOP_AGENT_URL}")
    add_log(f"OLD_CAMERA_BASE={OLD_CAMERA_BASE}")
    add_log(f"HAS_MEDIAPIPE={HAS_MEDIAPIPE}")
    ensure_old_camera_service()
    switch_old_camera_to_combined()
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
