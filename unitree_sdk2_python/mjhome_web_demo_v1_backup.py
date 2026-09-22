import os
import signal
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from flask import Flask, Response, jsonify, render_template_string, request

BASE_DIR = Path(__file__).resolve().parent

NETWORK_INTERFACE = "eth0"
DEFAULT_CAMERA_ID = "3"

app = Flask(__name__)

detection_process = None
log_lines = []
MAX_LOG_LINES = 400

camera_lock = threading.Lock()
camera_id_global = int(DEFAULT_CAMERA_ID)


def add_log(message: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    log_lines.append(line)

    if len(log_lines) > MAX_LOG_LINES:
        del log_lines[: len(log_lines) - MAX_LOG_LINES]


def run_command_async(cmd, label="command", timeout=120):
    def worker():
        add_log(f"START {label}: {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd,
                cwd=str(BASE_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
            )
            output = result.stdout.strip()
            if output:
                for line in output.splitlines()[-50:]:
                    add_log(f"{label}: {line}")
            add_log(f"END {label}, return code={result.returncode}")
        except subprocess.TimeoutExpired:
            add_log(f"TIMEOUT {label}")
        except Exception as e:
            add_log(f"ERROR {label}: {e}")

    t = threading.Thread(target=worker, daemon=True)
    t.start()


def generate_camera_frames():
    """
    MJPEG camera stream for browser.
    Default camera_id is controlled by camera_id_global.
    """
    last_camera_id = None
    cap = None

    while True:
        with camera_lock:
            current_camera_id = camera_id_global

        if cap is None or current_camera_id != last_camera_id:
            if cap is not None:
                cap.release()

            add_log(f"Opening camera {current_camera_id} for live view...")
            cap = cv2.VideoCapture(current_camera_id, cv2.CAP_V4L2)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            cap.set(cv2.CAP_PROP_FPS, 15)

            last_camera_id = current_camera_id
            time.sleep(0.5)

        ok, frame = cap.read()

        if not ok or frame is None:
            blank = 255 * (np.ones((480, 640, 3), dtype="uint8"))
            cv2.putText(
                blank,
                f"Camera {current_camera_id} not available",
                (60, 240),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.0,
                (0, 0, 255),
                2,
            )
            frame = blank
            time.sleep(0.2)

        # Add timestamp and camera id
        cv2.putText(
            frame,
            f"Camera {current_camera_id} | {datetime.now().strftime('%H:%M:%S')}",
            (15, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.75,
            (255, 255, 255),
            2,
        )

        ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + jpg.tobytes() + b"\r\n"
        )

        time.sleep(0.05)


@app.route("/")
def index():
    return render_template_string(
        """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>MJHome Robot Web Demo V1</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            background: #f4f6f8;
            margin: 0;
            padding: 0;
        }
        .header {
            background: #111827;
            color: white;
            padding: 20px 32px;
        }
        .header h1 {
            margin: 0;
            font-size: 28px;
        }
        .header p {
            margin: 8px 0 0 0;
            color: #cbd5e1;
        }
        .container {
            padding: 24px 32px;
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 24px;
        }
        .card {
            background: white;
            border-radius: 14px;
            padding: 22px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.08);
        }
        .card h2 {
            margin-top: 0;
            color: #111827;
        }
        button {
            border: none;
            border-radius: 10px;
            padding: 14px 20px;
            margin: 8px;
            font-size: 16px;
            cursor: pointer;
            background: #2563eb;
            color: white;
        }
        button:hover {
            background: #1d4ed8;
        }
        .danger {
            background: #dc2626;
        }
        .danger:hover {
            background: #b91c1c;
        }
        .safe {
            background: #16a34a;
        }
        .safe:hover {
            background: #15803d;
        }
        .warning {
            background: #f59e0b;
        }
        .warning:hover {
            background: #d97706;
        }
        .movement-grid {
            display: grid;
            grid-template-columns: repeat(3, 120px);
            gap: 8px;
            align-items: center;
        }
        .movement-grid button {
            width: 120px;
            height: 56px;
        }
        .empty {
            visibility: hidden;
        }
        .log-box {
            background: #020617;
            color: #d1d5db;
            padding: 16px;
            height: 360px;
            overflow-y: auto;
            border-radius: 10px;
            font-family: Consolas, monospace;
            font-size: 14px;
            white-space: pre-wrap;
        }
        .status {
            font-weight: bold;
            color: #111827;
        }
        .full {
            grid-column: 1 / 3;
        }
        input {
            padding: 10px;
            border-radius: 8px;
            border: 1px solid #cbd5e1;
            font-size: 15px;
            width: 80px;
        }
        .small-note {
            color: #64748b;
            font-size: 14px;
            line-height: 1.5;
        }
        .camera-view {
            width: 100%;
            max-width: 720px;
            border-radius: 12px;
            background: #000;
            border: 2px solid #111827;
        }
        .row {
            margin-top: 12px;
        }
        .tag {
            background: #e5e7eb;
            border-radius: 8px;
            padding: 4px 8px;
            font-size: 13px;
            color: #111827;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>MJHome Robot Web Demo V1</h1>
        <p>G1 EDU smart retail greeting system with live camera, customer-entry detection, voice greeting, face wave, and motion control.</p>
    </div>

    <div class="container">
        <div class="card">
            <h2>Live Camera View</h2>
            <img class="camera-view" id="camera_stream" src="/video_feed">
            <div class="row">
                <label>Camera ID:</label>
                <input id="camera_id" value="3">
                <button onclick="setCamera()">Set Camera</button>
                <button onclick="reloadCamera()">Reload View</button>
            </div>
            <p class="small-note">
                Current recommended built-in camera: <span class="tag">3</span>.
                If using external Type-C/USB RGB camera, change Camera ID to its detected number, e.g. 6.
            </p>
        </div>

        <div class="card">
            <h2>Greeting Control</h2>
            <button class="safe" onclick="api('/api/greet')">Manual Greet</button>
            <button class="safe" onclick="api('/api/start_detection')">Start Customer Detection</button>
            <button class="danger" onclick="api('/api/stop_detection')">Stop Detection</button>

            <hr style="margin: 18px 0;">
            <h3>Voice Messages Only</h3>
            <button class="safe" onclick="api('/api/say/1')">Say Greeting 1</button>
            <button class="safe" onclick="api('/api/say/2')">Say Greeting 2</button>
            <button class="safe" onclick="api('/api/say/3')">Say Greeting 3</button>
            <button class="warning" onclick="api('/api/say/random')">Say Random</button>
            <p class="small-note">
                These buttons only play the selected voice message. They do not trigger face wave or other gestures.
            </p>

            <p class="small-note">
                Start Detection uses the Camera ID above. When customer entry is detected, the robot says
                “Hello, I am MJHome Robot” and performs face wave.
            </p>
        </div>

        <div class="card">
            <h2>Robot Motion Control</h2>
            <div class="movement-grid">
                <button class="warning" onclick="move('turnleft')">Turn Left</button>
                <button onclick="move('forward')">Forward</button>
                <button class="warning" onclick="move('turnright')">Turn Right</button>

                <button onclick="move('left')">Left</button>
                <button class="danger" onclick="move('stop')">Stop</button>
                <button onclick="move('right')">Right</button>

                <button class="empty">.</button>
                <button onclick="move('back')">Back</button>
                <button class="empty">.</button>
            </div>
            <p class="small-note">
                Before movement control, set the robot in App:
                Damping → Preparation → Walk → Control Waist.
                Keep emergency stop ready.
            </p>
        </div>

        <div class="card">
            <h2>Demo Checklist</h2>
            <p class="small-note">
                1. Start robot and enter Walk / Control Waist in App.<br>
                2. Start this web demo on G1.<br>
                3. Open this page from Windows browser.<br>
                4. Check live camera view.<br>
                5. Try Manual Greet.<br>
                6. Start Customer Detection and walk in front of the camera.
            </p>
        </div>

        <div class="card full">
            <h2>Status and Logs</h2>
            <p class="status" id="status">Loading...</p>
            <button onclick="refreshLog()">Refresh Log</button>
            <button class="danger" onclick="api('/api/stop_detection')">Emergency Stop Detection</button>
            <div class="log-box" id="log"></div>
        </div>
    </div>

<script>
async function api(path) {
    let url = path;
    if (path === '/api/start_detection') {
        const cam = document.getElementById('camera_id').value;
        url = path + '?camera_id=' + encodeURIComponent(cam);
    }

    const res = await fetch(url, {method: 'POST'});
    const data = await res.json();
    document.getElementById('status').innerText = data.message;
    refreshLog();
}

async function move(cmd) {
    const res = await fetch('/api/move/' + cmd, {method: 'POST'});
    const data = await res.json();
    document.getElementById('status').innerText = data.message;
    refreshLog();
}

async function setCamera() {
    const cam = document.getElementById('camera_id').value;
    const res = await fetch('/api/set_camera?camera_id=' + encodeURIComponent(cam), {method: 'POST'});
    const data = await res.json();
    document.getElementById('status').innerText = data.message;
    reloadCamera();
    refreshLog();
}

function reloadCamera() {
    const img = document.getElementById('camera_stream');
    img.src = '/video_feed?t=' + new Date().getTime();
}

async function refreshLog() {
    const res = await fetch('/api/log');
    const data = await res.json();
    document.getElementById('log').innerText = data.log.join("\\n");
    const logBox = document.getElementById('log');
    logBox.scrollTop = logBox.scrollHeight;

    const statusRes = await fetch('/api/status');
    const statusData = await statusRes.json();
    document.getElementById('status').innerText = statusData.message;
}

setInterval(refreshLog, 2000);
refreshLog();
</script>
</body>
</html>
        """
    )


@app.route("/video_feed")
def video_feed():
    return Response(
        generate_camera_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/api/set_camera", methods=["POST"])
def set_camera():
    global camera_id_global

    cam = request.args.get("camera_id", DEFAULT_CAMERA_ID)

    try:
        cam_id = int(cam)
    except ValueError:
        return jsonify({"ok": False, "message": f"Invalid camera id: {cam}"})

    with camera_lock:
        camera_id_global = cam_id

    add_log(f"Camera switched to {cam_id}.")
    return jsonify({"ok": True, "message": f"Camera switched to {cam_id}."})


@app.route("/api/status", methods=["GET"])
def status():
    global detection_process

    if detection_process is not None and detection_process.poll() is None:
        return jsonify({"ok": True, "message": "Detection is running."})

    return jsonify({"ok": True, "message": "Detection is stopped."})


@app.route("/api/log", methods=["GET"])
def get_log():
    return jsonify({"ok": True, "log": log_lines})



@app.route("/api/say/<choice>", methods=["POST"])
def say_greeting(choice):
    allowed = {"1", "2", "3", "random"}

    if choice not in allowed:
        return jsonify({"ok": False, "message": f"Unknown voice choice: {choice}"})

    script = BASE_DIR / "mjhome_say.py"

    if not script.exists():
        msg = "mjhome_say.py not found."
        add_log(msg)
        return jsonify({"ok": False, "message": msg})

    run_command_async(
        ["python3", str(script), NETWORK_INTERFACE, choice],
        label=f"say_greeting_{choice}",
        timeout=120,
    )

    return jsonify({"ok": True, "message": f"Voice greeting {choice} started."})


@app.route("/api/greet", methods=["POST"])
def manual_greet():
    script = BASE_DIR / "mjhome_greet.py"

    if not script.exists():
        msg = "mjhome_greet.py not found."
        add_log(msg)
        return jsonify({"ok": False, "message": msg})

    run_command_async(
        ["python3", str(script), NETWORK_INTERFACE],
        label="manual_greet",
    )

    return jsonify({"ok": True, "message": "Manual greeting started."})


@app.route("/api/start_detection", methods=["POST"])
def start_detection():
    global detection_process

    if detection_process is not None and detection_process.poll() is None:
        return jsonify({"ok": True, "message": "Detection is already running."})

    camera_id = request.args.get("camera_id", DEFAULT_CAMERA_ID)

    # Prefer V3 shadow-filtered customer-entry detection.
    script = BASE_DIR / "mjhome_customer_entry_greet_v3.py"

    # Fallback to older version if V3 does not exist.
    if not script.exists():
        script = BASE_DIR / "mjhome_customer_entry_greet.py"

    if not script.exists():
        msg = "Customer detection script not found."
        add_log(msg)
        return jsonify({"ok": False, "message": msg})

    cmd = ["python3", str(script), NETWORK_INTERFACE, str(camera_id)]

    add_log(f"START detection: {' '.join(cmd)}")

    detection_process = subprocess.Popen(
        cmd,
        cwd=str(BASE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        preexec_fn=os.setsid,
        bufsize=1,
    )

    def reader():
        global detection_process
        try:
            for line in detection_process.stdout:
                add_log("detection: " + line.rstrip())
        except Exception as e:
            add_log(f"detection reader error: {e}")

    threading.Thread(target=reader, daemon=True).start()

    return jsonify({"ok": True, "message": f"Detection started with camera {camera_id}."})


@app.route("/api/stop_detection", methods=["POST"])
def stop_detection():
    global detection_process

    if detection_process is None or detection_process.poll() is not None:
        return jsonify({"ok": True, "message": "Detection is not running."})

    try:
        os.killpg(os.getpgid(detection_process.pid), signal.SIGINT)
        time.sleep(1.0)

        if detection_process.poll() is None:
            os.killpg(os.getpgid(detection_process.pid), signal.SIGTERM)

        add_log("Detection stopped.")
        return jsonify({"ok": True, "message": "Detection stopped."})

    except Exception as e:
        add_log(f"Failed to stop detection: {e}")
        return jsonify({"ok": False, "message": f"Failed to stop detection: {e}"})


@app.route("/api/move/<cmd>", methods=["POST"])
def move_robot(cmd):
    allowed = {"stop", "forward", "back", "left", "right", "turnleft", "turnright"}

    if cmd not in allowed:
        return jsonify({"ok": False, "message": f"Unknown movement command: {cmd}"})

    script = BASE_DIR / "g1_walk_cli.py"

    if not script.exists():
        msg = "g1_walk_cli.py not found."
        add_log(msg)
        return jsonify({"ok": False, "message": msg})

    run_command_async(
        ["python3", str(script), NETWORK_INTERFACE, cmd],
        label=f"move_{cmd}",
        timeout=120,
    )

    return jsonify({"ok": True, "message": f"Movement command sent: {cmd}"})


if __name__ == "__main__":
    add_log("MJHome Web Demo V1 starting...")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
