import os
import signal
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request

BASE_DIR = Path(__file__).resolve().parent

NETWORK_INTERFACE = "eth0"
DEFAULT_CAMERA_ID = "3"

app = Flask(__name__)

detection_process = None
log_lines = []
MAX_LOG_LINES = 300


def add_log(message: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    log_lines.append(line)

    if len(log_lines) > MAX_LOG_LINES:
        del log_lines[: len(log_lines) - MAX_LOG_LINES]


def run_command_async(cmd, label="command"):
    def worker():
        add_log(f"START {label}: {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd,
                cwd=str(BASE_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=60,
            )
            output = result.stdout.strip()
            if output:
                for line in output.splitlines()[-30:]:
                    add_log(f"{label}: {line}")
            add_log(f"END {label}, return code={result.returncode}")
        except subprocess.TimeoutExpired:
            add_log(f"TIMEOUT {label}")
        except Exception as e:
            add_log(f"ERROR {label}: {e}")

    t = threading.Thread(target=worker, daemon=True)
    t.start()


@app.route("/")
def index():
    return render_template_string(
        """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>MJHome Robot Control Demo</title>
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
    </style>
</head>
<body>
    <div class="header">
        <h1>MJHome Robot Control Demo</h1>
        <p>G1 EDU Smart Retail Greeting System: customer detection, voice greeting, face wave, and basic motion control.</p>
    </div>

    <div class="container">
        <div class="card">
            <h2>Greeting Control</h2>
            <button class="safe" onclick="api('/api/greet')">Manual Greet</button>
            <button class="safe" onclick="api('/api/start_detection')">Start Customer Detection</button>
            <button class="danger" onclick="api('/api/stop_detection')">Stop Detection</button>
            <p class="small-note">
                Start Detection will run the customer-entry detection script using camera ID below.
                When a person/motion is detected, the robot says “Hello, I am MJHome Robot” and performs face wave.
            </p>
            <label>Camera ID:</label>
            <input id="camera_id" value="3">
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
                Before movement control, set the robot in App: Damping → Preparation → Walk → Control Waist.
                Keep emergency stop ready.
            </p>
        </div>

        <div class="card full">
            <h2>Status</h2>
            <p class="status" id="status">Loading...</p>
            <button onclick="refreshLog()">Refresh Log</button>
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


@app.route("/api/status", methods=["GET"])
def status():
    global detection_process

    if detection_process is not None and detection_process.poll() is None:
        return jsonify({"ok": True, "message": "Detection is running."})

    return jsonify({"ok": True, "message": "Detection is stopped."})


@app.route("/api/log", methods=["GET"])
def get_log():
    return jsonify({"ok": True, "log": log_lines})


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
    script = BASE_DIR / "mjhome_customer_entry_greet.py"

    if not script.exists():
        msg = "mjhome_customer_entry_greet.py not found."
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
    )

    return jsonify({"ok": True, "message": f"Movement command sent: {cmd}"})


if __name__ == "__main__":
    add_log("MJHome Web Demo starting...")
    app.run(host="0.0.0.0", port=5000, debug=False)
