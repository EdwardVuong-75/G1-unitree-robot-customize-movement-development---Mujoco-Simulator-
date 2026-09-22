import json
import os
import subprocess
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request

BASE_DIR = Path(__file__).resolve().parent
TMP_DIR = BASE_DIR / "tmp"
TMP_DIR.mkdir(exist_ok=True)

LAPTOP_AGENT_URL = os.environ.get("LAPTOP_AGENT_URL", "http://192.168.123.100:5020")
NETWORK_INTERFACE = os.environ.get("NETWORK_INTERFACE", "eth0")
MIC_DEVICE = os.environ.get("MIC_DEVICE", "default")
PORT = int(os.environ.get("PORT", "5012"))

G1_WALK_CLI = Path(os.environ.get("G1_WALK_CLI", "/home/unitree/unitree_sdk2_python/g1_walk_cli.py"))
MJHOME_SAY_SCRIPT = Path(os.environ.get("MJHOME_SAY_SCRIPT", "/home/unitree/unitree_sdk2_python/mjhome_say.py"))

app = Flask(__name__)

log_lines = []
MAX_LOG_LINES = 500
last_result = None
last_stt_text = ""
motion_enabled = False


def add_log(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    log_lines.append(line)
    if len(log_lines) > MAX_LOG_LINES:
        del log_lines[: len(log_lines) - MAX_LOG_LINES]


def run_async(cmd, label="cmd", timeout=120):
    def worker():
        add_log(f"START {label}: {' '.join(str(x) for x in cmd)}")
        try:
            r = subprocess.run(
                [str(x) for x in cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
            )
            out = r.stdout.strip()
            if out:
                for line in out.splitlines()[-80:]:
                    add_log(f"{label}: {line}")
            add_log(f"END {label}, return code={r.returncode}")
        except Exception as e:
            add_log(f"ERROR {label}: {e}")

    threading.Thread(target=worker, daemon=True).start()


def post_json(url, payload, timeout=90):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def laptop_chat_text(text):
    return post_json(
        LAPTOP_AGENT_URL.rstrip("/") + "/api/chat_text",
        {"text": text},
        timeout=90
    )


def record_wav(seconds=5):
    wav_path = TMP_DIR / "robot_voice_once.wav"

    cmd = ["arecord"]

    if MIC_DEVICE and MIC_DEVICE != "default":
        cmd += ["-D", MIC_DEVICE]

    cmd += [
        "-f", "S16_LE",
        "-r", "16000",
        "-c", "1",
        "-d", str(seconds),
        "-q",
        str(wav_path)
    ]

    add_log(f"Recording {seconds}s from MIC_DEVICE={MIC_DEVICE}")
    subprocess.run(cmd, check=True)
    return wav_path


def laptop_audio_chat(wav_path):
    url = LAPTOP_AGENT_URL.rstrip("/") + "/api/audio_chat"

    cmd = [
        "curl",
        "-s",
        "-X", "POST",
        "-F", f"audio=@{wav_path}",
        url
    ]

    add_log(f"Uploading audio to laptop: {url}")
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)

    if r.returncode != 0:
        raise RuntimeError(r.stdout)

    return json.loads(r.stdout)


def speak_result(result):
    choice = str(result.get("tts_choice", "random"))
    if choice not in {"1", "2", "3", "random"}:
        choice = "random"

    if not MJHOME_SAY_SCRIPT.exists():
        return False, f"mjhome_say.py not found at {MJHOME_SAY_SCRIPT}"

    run_async(
        ["python3", MJHOME_SAY_SCRIPT, NETWORK_INTERFACE, choice],
        label=f"speak_{choice}",
        timeout=120,
    )
    return True, f"Speech started with choice {choice}."


def execute_result_action(result):
    global motion_enabled

    action = str(result.get("action", "none"))
    motion = str(result.get("motion", "none"))

    if action in {"none", "say", "call_staff"}:
        return True, "No robot motion required."

    if action == "stop":
        motion = "stop"

    if action == "guide_to_section":
        if motion == "none":
            return True, "Guide action suggested, but no motion configured."
        return execute_motion(motion)

    if action in {"forward", "back", "left", "right", "turnleft", "turnright"}:
        return execute_motion(action)

    return False, f"Unsupported action: {action}"


def execute_motion(cmd):
    global motion_enabled

    allowed = {"stop", "forward", "back", "left", "right", "turnleft", "turnright"}
    if cmd not in allowed:
        return False, f"Unknown motion command: {cmd}"

    if cmd != "stop" and not motion_enabled:
        return False, "Motion is disabled. Click Enable Motion first."

    if not G1_WALK_CLI.exists():
        return False, f"g1_walk_cli.py not found at {G1_WALK_CLI}"

    run_async(
        ["python3", G1_WALK_CLI, NETWORK_INTERFACE, cmd],
        label=f"move_{cmd}",
        timeout=120,
    )
    return True, f"Motion command sent: {cmd}"


@app.route("/")
def index():
    return render_template_string("""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Shosha Robot Voice LAN</title>
    <style>
        body { font-family: Arial, sans-serif; background:#0f172a; color:#f8fafc; margin:0; }
        .header { background:#020617; padding:20px 32px; border-bottom:1px solid #334155; }
        .header h1 { margin:0; font-size:30px; }
        .header p { color:#cbd5e1; }
        .container { padding:24px 32px; display:grid; grid-template-columns:2fr 1fr; gap:22px; }
        .card { background:#1e293b; border:1px solid #475569; border-radius:16px; padding:20px; }
        .full { grid-column:1 / 3; }
        textarea, input {
            width:100%; box-sizing:border-box; border-radius:10px; border:1px solid #475569;
            background:#020617; color:#f8fafc; padding:12px; font-size:16px;
        }
        textarea { min-height:90px; }
        button {
            border:none; border-radius:10px; padding:13px 16px; margin:6px 6px 6px 0;
            font-size:15px; font-weight:700; cursor:pointer; background:#2563eb; color:white;
        }
        .danger { background:#dc2626; }
        .safe { background:#16a34a; }
        .warning { background:#f59e0b; }
        .purple { background:#7c3aed; }
        .gray { background:#64748b; }
        .output, .jsonbox, .logbox {
            background:#020617; color:#d1d5db; padding:16px; border-radius:10px;
            border:1px solid #334155; white-space:pre-wrap;
        }
        .jsonbox { color:#c4b5fd; font-family:Consolas, monospace; min-height:190px; }
        .logbox { height:300px; overflow-y:auto; font-family:Consolas, monospace; font-size:13px; }
        .grid3 { display:grid; grid-template-columns:repeat(3,1fr); gap:9px; }
        .grid3 button { width:100%; min-height:58px; margin:0; }
        .empty { visibility:hidden; }
    </style>
</head>
<body>
<div class="header">
    <h1>Shosha Robot Voice LAN</h1>
    <p>Robot records audio, laptop runs Vosk + Qwen 7B, robot executes safe actions.</p>
</div>

<div class="container">
    <div class="card">
        <h2>Conversation</h2>
        <h3>Text Input</h3>
        <textarea id="text_input" placeholder="Example: Hello, where are the pods?"></textarea>
        <button class="safe" onclick="sendText()">Send Text</button>

        <h3>Robot Microphone</h3>
        <label>Record seconds:</label>
        <input id="seconds" value="5">
        <button class="purple" onclick="voiceOnce()">Voice Once</button>

        <h3>STT Result</h3>
        <div class="output" id="stt">No voice yet.</div>

        <h3>Robot Reply</h3>
        <div class="output" id="reply">No reply yet.</div>

        <h3>Safe Action JSON</h3>
        <div class="jsonbox" id="json">{}</div>
    </div>

    <div class="card">
        <h2>Controls</h2>
        <button class="safe" onclick="sayReply()">Say Reply</button>
        <button class="warning" onclick="executeAction()">Execute Safe Action</button>
        <button class="purple" onclick="motionEnable(true)">Enable Motion</button>
        <button class="gray" onclick="motionEnable(false)">Disable Motion</button>
        <button class="danger" onclick="move('stop')">Emergency STOP</button>

        <h3>Manual Move</h3>
        <div class="grid3">
            <button class="warning" onclick="move('turnleft')">↺ Turn Left</button>
            <button onclick="move('forward')">↑ Forward</button>
            <button class="warning" onclick="move('turnright')">Turn Right ↻</button>
            <button onclick="move('left')">← Left</button>
            <button class="danger" onclick="move('stop')">STOP</button>
            <button onclick="move('right')">Right →</button>
            <button class="empty">.</button>
            <button onclick="move('back')">↓ Back</button>
            <button class="empty">.</button>
        </div>

        <h3>Status</h3>
        <div class="output" id="status">Loading...</div>
    </div>

    <div class="card full">
        <h2>Logs</h2>
        <button onclick="refresh()">Refresh</button>
        <button class="danger" onclick="move('stop')">Emergency STOP</button>
        <div class="logbox" id="log"></div>
    </div>
</div>

<script>
function show(data) {
    console.log(data);
    if (data.stt_text !== undefined) document.getElementById('stt').innerText = data.stt_text || '(empty)';
    if (data.result) {
        document.getElementById('reply').innerText = data.result.reply || '';
        document.getElementById('json').innerText = JSON.stringify(data.result, null, 2);
    }
    if (data.message && !data.result) document.getElementById('status').innerText = data.message;
    if (data.ok === false) alert(data.message || 'Error');
    refresh();
}
async function post(path, body={}) {
    const res = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    const data = await res.json();
    show(data);
    return data;
}
async function sendText() {
    await post('/api/chat_text', {text: document.getElementById('text_input').value});
}
async function voiceOnce() {
    document.getElementById('stt').innerText = 'Recording...';
    document.getElementById('reply').innerText = 'Waiting...';
    await post('/api/voice_once', {seconds: parseInt(document.getElementById('seconds').value || '5')});
}
async function sayReply() { await post('/api/say_reply'); }
async function executeAction() { await post('/api/execute_action'); }
async function motionEnable(v) { await post('/api/motion_enable', {enabled:v}); }
async function move(cmd) { await post('/api/move/' + cmd); }
async function refresh() {
    const l = await fetch('/api/log').then(r=>r.json());
    document.getElementById('log').innerText = l.log.join("\\n");
    const box = document.getElementById('log'); box.scrollTop = box.scrollHeight;
    const s = await fetch('/api/status').then(r=>r.json());
    document.getElementById('status').innerText = s.message;
}
setInterval(refresh, 2000);
refresh();
</script>
</body>
</html>
""")


@app.route("/api/chat_text", methods=["POST"])
def api_chat_text():
    global last_result

    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()
    add_log(f"USER TEXT: {text}")

    try:
        response = laptop_chat_text(text)
        last_result = response.get("result")
        add_log(f"REPLY: {last_result.get('reply') if last_result else 'none'}")
        return jsonify({"ok": True, "user_text": text, "result": last_result})
    except Exception as e:
        add_log(f"TEXT ERROR: {e}")
        return jsonify({"ok": False, "message": str(e)})


@app.route("/api/voice_once", methods=["POST"])
def api_voice_once():
    global last_result, last_stt_text

    data = request.get_json(silent=True) or {}
    seconds = int(data.get("seconds", 5))
    seconds = max(2, min(seconds, 10))

    try:
        wav = record_wav(seconds)
        response = laptop_audio_chat(wav)

        last_stt_text = response.get("stt_text", "")
        last_result = response.get("result")

        add_log(f"STT: {last_stt_text}")
        add_log(f"REPLY: {last_result.get('reply') if last_result else 'none'}")

        return jsonify({
            "ok": response.get("ok", False),
            "stt_text": last_stt_text,
            "result": last_result,
            "message": response.get("message", "")
        })

    except Exception as e:
        add_log(f"VOICE ERROR: {e}")
        return jsonify({"ok": False, "message": str(e)})


@app.route("/api/say_reply", methods=["POST"])
def api_say_reply():
    if not last_result:
        return jsonify({"ok": False, "message": "No reply yet."})

    ok, msg = speak_result(last_result)
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/execute_action", methods=["POST"])
def api_execute_action():
    if not last_result:
        return jsonify({"ok": False, "message": "No action yet."})

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


@app.route("/api/status")
def api_status():
    return jsonify({
        "ok": True,
        "message": (
            f"Laptop Agent URL: {LAPTOP_AGENT_URL}\\n"
            f"MIC_DEVICE: {MIC_DEVICE}\\n"
            f"Motion enabled: {motion_enabled}\\n"
            f"G1 walk script: {G1_WALK_CLI}\\n"
            f"Say script: {MJHOME_SAY_SCRIPT}\\n"
            f"Last STT: {last_stt_text}"
        )
    })


@app.route("/api/log")
def api_log():
    return jsonify({"ok": True, "log": log_lines})


if __name__ == "__main__":
    add_log("Robot Voice Frontend starting...")
    add_log(f"LAPTOP_AGENT_URL={LAPTOP_AGENT_URL}")
    add_log(f"MIC_DEVICE={MIC_DEVICE}")
    add_log(f"PORT={PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
