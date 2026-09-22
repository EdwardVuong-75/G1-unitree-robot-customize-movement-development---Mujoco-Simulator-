import os
import sys
import json
import wave
import audioop
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

PORT = int(os.environ.get("PORT", "5013"))

# BOYA MINI 2 已经在你的机器上显示为 card 2, device 0
BOYA_MIC_DEVICE = os.environ.get("BOYA_MIC_DEVICE", "plughw:2,0")

# 笔记本 Agent Server
LAPTOP_AGENT_URL = os.environ.get("LAPTOP_AGENT_URL", "http://192.168.123.100:5020")

NETWORK_INTERFACE = os.environ.get("NETWORK_INTERFACE", "eth0")

G1_WALK_CLI = Path(os.environ.get("G1_WALK_CLI", "/home/unitree/unitree_sdk2_python/g1_walk_cli.py"))
MJHOME_SAY_SCRIPT = Path(os.environ.get("MJHOME_SAY_SCRIPT", "/home/unitree/unitree_sdk2_python/mjhome_say.py"))

app = Flask(__name__)

log_lines = []
MAX_LOG_LINES = 600

last_result = None
last_stt_text = ""
last_wav_path = ""
motion_enabled = False


def add_log(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    log_lines.append(line)
    if len(log_lines) > MAX_LOG_LINES:
        del log_lines[: len(log_lines) - MAX_LOG_LINES]


def analyze_wav(path):
    try:
        wf = wave.open(str(path), "rb")
        data = wf.readframes(wf.getnframes())
        wf.close()

        size = os.path.getsize(path)
        rms = audioop.rms(data, 2) if data else 0
        peak = audioop.max(data, 2) if data else 0

        return {
            "path": str(path),
            "size": size,
            "rms": rms,
            "peak": peak,
        }
    except Exception as e:
        return {
            "path": str(path),
            "size": 0,
            "rms": 0,
            "peak": 0,
            "error": str(e),
        }


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
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def chat_text_with_laptop(text):
    return post_json(
        LAPTOP_AGENT_URL.rstrip("/") + "/api/chat_text",
        {"text": text},
        timeout=120,
    )


def record_boya(seconds=5):
    """
    BOYA MINI 2 专用录音：
    1. 固定从 plughw:2,0 录音
    2. 先用 48k stereo，兼容无线 USB 麦
    3. 用 ffmpeg 转成 Vosk 需要的 16k mono
    4. 加高通/低通/动态归一化/增益，解决 BOYA 音量偏低问题
    """
    seconds = max(2, min(int(seconds), 10))

    raw_path = TMP_DIR / "boya_raw_48k.wav"
    wav_path = TMP_DIR / "boya_processed_16k.wav"

    add_log(f"Recording BOYA: device={BOYA_MIC_DEVICE}, seconds={seconds}")

    cmd_record_48k = [
        "arecord",
        "-D", BOYA_MIC_DEVICE,
        "-f", "S16_LE",
        "-r", "48000",
        "-c", "2",
        "-d", str(seconds),
        "-q",
        str(raw_path),
    ]

    try:
        subprocess.run(cmd_record_48k, check=True)
        add_log("48k stereo recording OK.")
    except Exception as e:
        add_log(f"48k stereo recording failed: {e}")
        add_log("Trying 16k mono fallback...")

        raw_path = TMP_DIR / "boya_raw_16k.wav"
        cmd_record_16k = [
            "arecord",
            "-D", BOYA_MIC_DEVICE,
            "-f", "S16_LE",
            "-r", "16000",
            "-c", "1",
            "-d", str(seconds),
            "-q",
            str(raw_path),
        ]
        subprocess.run(cmd_record_16k, check=True)

    raw_stats = analyze_wav(raw_path)
    add_log(f"Raw audio stats: size={raw_stats['size']}, rms={raw_stats['rms']}, peak={raw_stats['peak']}")

    cmd_convert = [
        "ffmpeg",
        "-y",
        "-loglevel", "error",
        "-i", str(raw_path),
        "-ac", "1",
        "-ar", "16000",
        "-af", "highpass=f=120,lowpass=f=7000,dynaudnorm=f=150:g=15:p=0.95,volume=24dB",
        str(wav_path),
    ]

    subprocess.run(cmd_convert, check=True)

    out_stats = analyze_wav(wav_path)
    add_log(f"Processed audio stats: size={out_stats['size']}, rms={out_stats['rms']}, peak={out_stats['peak']}")

    return wav_path, raw_stats, out_stats


def upload_audio_to_laptop(wav_path):
    url = LAPTOP_AGENT_URL.rstrip("/") + "/api/audio_chat"

    cmd = [
        "curl",
        "-s",
        "-X", "POST",
        "-F", f"audio=@{wav_path}",
        url,
    ]

    add_log(f"Uploading audio to laptop agent: {url}")

    r = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=180,
    )

    if r.returncode != 0:
        raise RuntimeError(r.stdout)

    try:
        return json.loads(r.stdout)
    except Exception:
        raise RuntimeError("Laptop agent returned non-JSON response: " + r.stdout[:500])


def speak_result(result):
    if not result:
        return False, "No result to speak."

    choice = str(result.get("tts_choice", "random"))
    if choice not in {"1", "2", "3", "random"}:
        choice = "random"

    if not MJHOME_SAY_SCRIPT.exists():
        return False, f"mjhome_say.py not found: {MJHOME_SAY_SCRIPT}"

    run_async(
        ["python3", MJHOME_SAY_SCRIPT, NETWORK_INTERFACE, choice],
        label=f"speak_{choice}",
        timeout=120,
    )
    return True, f"Speech started with choice {choice}."


def execute_motion(cmd):
    global motion_enabled

    allowed = {"stop", "forward", "back", "left", "right", "turnleft", "turnright"}

    if cmd not in allowed:
        return False, f"Unknown motion command: {cmd}"

    if cmd != "stop" and not motion_enabled:
        return False, "Motion is disabled. Click Enable Motion first."

    if not G1_WALK_CLI.exists():
        return False, f"g1_walk_cli.py not found: {G1_WALK_CLI}"

    run_async(
        ["python3", G1_WALK_CLI, NETWORK_INTERFACE, cmd],
        label=f"move_{cmd}",
        timeout=120,
    )
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


@app.route("/")
def index():
    return render_template_string("""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>BOYA Robot Voice Test</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            background: #0f172a;
            color: #f8fafc;
            margin: 0;
        }
        .header {
            background: #020617;
            padding: 20px 32px;
            border-bottom: 1px solid #334155;
        }
        .header h1 {
            margin: 0;
            font-size: 30px;
        }
        .header p {
            color: #cbd5e1;
        }
        .container {
            padding: 24px 32px;
            display: grid;
            grid-template-columns: 2fr 1fr;
            gap: 22px;
        }
        .card {
            background: #1e293b;
            border: 1px solid #475569;
            border-radius: 16px;
            padding: 20px;
        }
        .full {
            grid-column: 1 / 3;
        }
        textarea, input {
            width: 100%;
            box-sizing: border-box;
            border-radius: 10px;
            border: 1px solid #475569;
            background: #020617;
            color: #f8fafc;
            padding: 12px;
            font-size: 16px;
        }
        textarea {
            min-height: 90px;
        }
        button {
            border: none;
            border-radius: 10px;
            padding: 13px 16px;
            margin: 6px 6px 6px 0;
            font-size: 15px;
            font-weight: 700;
            cursor: pointer;
            background: #2563eb;
            color: white;
        }
        button:hover {
            opacity: 0.86;
        }
        .danger { background: #dc2626; }
        .safe { background: #16a34a; }
        .warning { background: #f59e0b; }
        .purple { background: #7c3aed; }
        .gray { background: #64748b; }
        .output, .jsonbox, .logbox {
            background: #020617;
            color: #d1d5db;
            padding: 16px;
            border-radius: 10px;
            border: 1px solid #334155;
            white-space: pre-wrap;
        }
        .jsonbox {
            color: #c4b5fd;
            font-family: Consolas, monospace;
            min-height: 190px;
        }
        .logbox {
            height: 320px;
            overflow-y: auto;
            font-family: Consolas, monospace;
            font-size: 13px;
        }
        .grid3 {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 9px;
            margin-top: 12px;
        }
        .grid3 button {
            width: 100%;
            min-height: 58px;
            margin: 0;
        }
        .empty {
            visibility: hidden;
        }
        @media (max-width: 1100px) {
            .container { grid-template-columns: 1fr; }
            .full { grid-column: 1; }
        }
    </style>
</head>
<body>
<div class="header">
    <h1>BOYA Robot Voice Test</h1>
    <p>BOYA MINI 2 → Robot recording → Laptop Agent → Shosha reply + safe action.</p>
</div>

<div class="container">
    <div class="card">
        <h2>BOYA Voice Once</h2>
        <label>Record seconds:</label>
        <input id="seconds" value="5">

        <button class="purple" onclick="voiceOnce()">Voice Once with BOYA</button>

        <h3>Text Test</h3>
        <textarea id="text_input" placeholder="Example: Where are the pods?"></textarea>
        <button class="safe" onclick="sendText()">Send Text</button>

        <h3>STT Result</h3>
        <div class="output" id="stt">No voice yet.</div>

        <h3>Robot Reply</h3>
        <div class="output" id="reply">No reply yet.</div>

        <h3>Safe Action JSON</h3>
        <div class="jsonbox" id="json">{}</div>
    </div>

    <div class="card">
        <h2>Robot Controls</h2>
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

    if (data.stt_text !== undefined) {
        document.getElementById("stt").innerText = data.stt_text || "(empty)";
    }

    if (data.result) {
        document.getElementById("reply").innerText = data.result.reply || "";
        document.getElementById("json").innerText = JSON.stringify(data.result, null, 2);
    }

    if (data.message && !data.result) {
        document.getElementById("status").innerText = data.message;
    }

    if (data.ok === false) {
        alert(data.message || "Error");
    }

    refresh();
}

async function post(path, body={}) {
    const res = await fetch(path, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body)
    });
    const data = await res.json();
    show(data);
    return data;
}

async function voiceOnce() {
    const seconds = parseInt(document.getElementById("seconds").value || "5");
    document.getElementById("stt").innerText = "Recording with BOYA...";
    document.getElementById("reply").innerText = "Waiting...";
    await post("/api/voice_once", {seconds});
}

async function sendText() {
    const text = document.getElementById("text_input").value;
    await post("/api/chat_text", {text});
}

async function sayReply() {
    await post("/api/say_reply");
}

async function executeAction() {
    await post("/api/execute_action");
}

async function motionEnable(enabled) {
    await post("/api/motion_enable", {enabled});
}

async function move(cmd) {
    await post("/api/move/" + cmd);
}

async function refresh() {
    const logRes = await fetch("/api/log");
    const logData = await logRes.json();
    document.getElementById("log").innerText = logData.log.join("\\n");
    const box = document.getElementById("log");
    box.scrollTop = box.scrollHeight;

    const statusRes = await fetch("/api/status");
    const statusData = await statusRes.json();
    document.getElementById("status").innerText = statusData.message;
}

setInterval(refresh, 2000);
refresh();
</script>
</body>
</html>
""")


@app.route("/api/voice_once", methods=["POST"])
def api_voice_once():
    global last_result, last_stt_text, last_wav_path

    data = request.get_json(silent=True) or {}
    seconds = int(data.get("seconds", 5))
    seconds = max(2, min(seconds, 10))

    try:
        wav_path, raw_stats, processed_stats = record_boya(seconds)
        last_wav_path = str(wav_path)

        response = upload_audio_to_laptop(wav_path)

        last_stt_text = response.get("stt_text", "")
        last_result = response.get("result")

        add_log(f"STT: {last_stt_text}")
        if last_result:
            add_log(f"REPLY: {last_result.get('reply')}")
            add_log(f"ACTION: {last_result.get('action')} | MOTION: {last_result.get('motion')}")

        return jsonify({
            "ok": response.get("ok", False),
            "stt_text": last_stt_text,
            "result": last_result,
            "raw_stats": raw_stats,
            "processed_stats": processed_stats,
            "wav_path": str(wav_path),
            "message": response.get("message", "")
        })

    except Exception as e:
        add_log(f"VOICE ERROR: {e}")
        return jsonify({"ok": False, "message": str(e)})


@app.route("/api/chat_text", methods=["POST"])
def api_chat_text():
    global last_result

    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()

    if not text:
        return jsonify({"ok": False, "message": "Empty text."})

    try:
        response = chat_text_with_laptop(text)
        last_result = response.get("result")

        return jsonify({
            "ok": response.get("ok", True),
            "stt_text": "",
            "result": last_result,
            "message": response.get("message", "")
        })

    except Exception as e:
        add_log(f"TEXT ERROR: {e}")
        return jsonify({"ok": False, "message": str(e)})


@app.route("/api/say_reply", methods=["POST"])
def api_say_reply():
    ok, msg = speak_result(last_result)
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


@app.route("/api/status")
def api_status():
    return jsonify({
        "ok": True,
        "message": (
            f"BOYA_MIC_DEVICE: {BOYA_MIC_DEVICE}\\n"
            f"LAPTOP_AGENT_URL: {LAPTOP_AGENT_URL}\\n"
            f"PORT: {PORT}\\n"
            f"Motion enabled: {motion_enabled}\\n"
            f"Last STT: {last_stt_text}\\n"
            f"Last WAV: {last_wav_path}\\n"
            f"G1 walk script: {G1_WALK_CLI}\\n"
            f"Say script: {MJHOME_SAY_SCRIPT}"
        )
    })


@app.route("/api/log")
def api_log():
    return jsonify({"ok": True, "log": log_lines})


def run_cli_test(seconds=5):
    print("=== BOYA CLI TEST ===")
    print("BOYA_MIC_DEVICE:", BOYA_MIC_DEVICE)
    print("LAPTOP_AGENT_URL:", LAPTOP_AGENT_URL)
    print("Say into BOYA transmitter now.")

    wav_path, raw_stats, processed_stats = record_boya(seconds)
    print("Raw stats:", json.dumps(raw_stats, indent=2))
    print("Processed stats:", json.dumps(processed_stats, indent=2))

    response = upload_audio_to_laptop(wav_path)
    print("Laptop response:")
    print(json.dumps(response, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        sec = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        run_cli_test(sec)
    else:
        add_log("BOYA Robot Voice Server starting...")
        add_log(f"BOYA_MIC_DEVICE={BOYA_MIC_DEVICE}")
        add_log(f"LAPTOP_AGENT_URL={LAPTOP_AGENT_URL}")
        add_log(f"PORT={PORT}")
        app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
