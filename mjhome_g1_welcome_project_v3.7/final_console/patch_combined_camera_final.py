from pathlib import Path

app_path = Path("app_final_console.py")
html_path = Path("templates/final_console.html")

s = app_path.read_text()

# ------------------------------------------------------------
# 1. Add OLD_CAMERA_BASE config.
# ------------------------------------------------------------
if "OLD_CAMERA_BASE" not in s:
    s = s.replace(
        'LAPTOP_AGENT_URL = os.environ.get("LAPTOP_AGENT_URL", "http://192.168.123.100:5020")',
        'LAPTOP_AGENT_URL = os.environ.get("LAPTOP_AGENT_URL", "http://192.168.123.100:5020")\nOLD_CAMERA_BASE = os.environ.get("OLD_CAMERA_BASE", "http://127.0.0.1:5010")'
    )

# ------------------------------------------------------------
# 2. Add helper to set old camera service to rs_combined.
# ------------------------------------------------------------
helper = r'''
def set_old_camera_combined():
    """
    Tell the old working camera service to switch to its combined RGB+Depth mode.
    This service is started by:
      cd ~/mjhome_g1_welcome_project
      PORT=5010 ./start.sh
    """
    try:
        url = OLD_CAMERA_BASE.rstrip("/") + "/api/set_camera"
        payload = json.dumps({"mode": "rs_combined"}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
        add_log("Old camera service switched to rs_combined.")
    except Exception as e:
        add_log(f"Could not switch old camera service to rs_combined: {e}")


def draw_combined_labels(frame):
    h, w = frame.shape[:2]
    mid = w // 2

    cv2.line(frame, (mid, 0), (mid, h), (255, 255, 255), 2)
    cv2.putText(frame, "RGB + MediaPipe", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255,255,255), 2)
    cv2.putText(frame, "Depth", (mid + 20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255,255,255), 2)

    return frame


def apply_vision_to_combined_frame(frame):
    """
    Combined frame is expected to be RGB on the left and Depth on the right.
    MediaPipe is applied only to the left RGB half.
    """
    if frame is None:
        return frame

    if len(frame.shape) == 2:
        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

    h, w = frame.shape[:2]

    # Combined stream is normally wide. If it is not wide, treat the whole image as RGB.
    if w >= int(h * 1.7):
        mid = w // 2
        left = frame[:, :mid].copy()
        right = frame[:, mid:].copy()

        try:
            left = apply_vision(left)
        except Exception as e:
            add_log(f"MediaPipe combined-left error: {e}")
            cv2.putText(left, "MediaPipe error", (20, 85), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,0,255), 2)

        frame = cv2.hconcat([left, right])
        frame = draw_combined_labels(frame)

    else:
        try:
            frame = apply_vision(frame)
        except Exception as e:
            add_log(f"MediaPipe single-frame error: {e}")
        cv2.putText(frame, "RGB", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255,255,255), 2)

    return frame


def gen_combined_from_old_camera():
    """
    Use the old known-good combined RGB+Depth camera stream.
    No separate RGB/Depth pipeline is opened in final_console.
    """
    set_old_camera_combined()

    candidates = [
        OLD_CAMERA_BASE.rstrip("/") + "/video_feed",
        OLD_CAMERA_BASE.rstrip("/") + "/video/realsense/combined",
    ]

    while True:
        for url in candidates:
            add_log(f"Opening combined camera stream: {url}")

            cap = cv2.VideoCapture(url)

            if not cap.isOpened():
                add_log(f"Could not open combined stream: {url}")
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

                    frame = apply_vision_to_combined_frame(frame)

                    # Keep combined aspect ratio. It should be roughly 1280x480.
                    h, w = frame.shape[:2]
                    if w < 1000:
                        frame = cv2.resize(frame, (1280, 480))

                    jpg = encode_jpg(frame)
                    if jpg:
                        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"

                    time.sleep(0.05)

            finally:
                try:
                    cap.release()
                except Exception:
                    pass

        img = camera_blank(
            "Combined camera service unavailable. Start: cd ~/mjhome_g1_welcome_project && PORT=5010 ./start.sh",
            width=1280,
            height=480
        )
        jpg = encode_jpg(img)
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
        time.sleep(1.0)

'''

if "def gen_combined_from_old_camera():" not in s:
    s = s.replace("\n@app.route(\"/\")", "\n" + helper + "\n@app.route(\"/\")")

# ------------------------------------------------------------
# 3. Replace /video_feed route to only use combined stream.
# ------------------------------------------------------------
start = s.index('@app.route("/video_feed")')
end = s.index('\n\n@app.route("/api/set_camera"', start)

new_video_feed = r'''@app.route("/video_feed")
def video_feed():
    return Response(
        gen_combined_from_old_camera(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )
'''

s = s[:start] + new_video_feed + s[end:]

# ------------------------------------------------------------
# 4. Make set_camera endpoint harmless.
# ------------------------------------------------------------
start = s.index('@app.route("/api/set_camera"')
end = s.index('\n\n@app.route("/api/set_vision"', start)

new_set_camera = r'''@app.route("/api/set_camera", methods=["POST"])
def api_set_camera():
    # Final version uses combined RGB+Depth only.
    set_old_camera_combined()
    return jsonify({"ok": True, "message": "Camera mode: combined RGB + Depth"})
'''

s = s[:start] + new_set_camera + s[end:]

# ------------------------------------------------------------
# 5. Lower voice ignore threshold; BOYA sometimes has usable but modest RMS.
# ------------------------------------------------------------
s = s.replace('if proc_stats.get("rms", 0) < 80:', 'if proc_stats.get("rms", 0) < 30:')

# ------------------------------------------------------------
# 6. Add OLD_CAMERA_BASE to status.
# ------------------------------------------------------------
if "Old camera service:" not in s:
    s = s.replace(
        'f"Laptop agent: {LAPTOP_AGENT_URL}\\n"',
        'f"Laptop agent: {LAPTOP_AGENT_URL}\\n"\n        f"Old camera service: {OLD_CAMERA_BASE}\\n"'
    )

app_path.write_text(s)

# ------------------------------------------------------------
# 7. Patch HTML: remove RGB/Depth buttons, keep vision buttons, set combined aspect.
# ------------------------------------------------------------
h = html_path.read_text()

# Camera aspect ratio: combined stream is wide.
h = h.replace('aspect-ratio: 16/9;', 'aspect-ratio: 8/3;')

if "button.selected" not in h:
    h = h.replace(
        "button.active { background: #ef4444; border-color: #ef4444; }",
        "button.active { background: #ef4444; border-color: #ef4444; }\nbutton.selected { background: #e5e7eb; color:#0b0d10; border-color:#e5e7eb; }"
    )

old_block = '''      <div class="row" style="margin-top:10px; flex-wrap: wrap;">
        <button onclick="setCamera('rgb')" class="primary">RGB</button>
        <button onclick="setCamera('depth')" class="ghost">Depth</button>
        <button onclick="setVision('off')" class="ghost">Vision Off</button>
        <button onclick="setVision('face')" class="ghost">Face</button>
        <button onclick="setVision('pose')" class="ghost">Pose</button>
        <button onclick="setVision('both')" class="ghost">Face + Pose</button>
      </div>'''

new_block = '''      <div class="row" style="margin-top:10px; flex-wrap: wrap;">
        <button class="primary selected">Combined RGB + Depth</button>
        <button id="btn-vision-off" onclick="setVision('off')" class="ghost selected">Vision Off</button>
        <button id="btn-vision-face" onclick="setVision('face')" class="ghost">Face</button>
        <button id="btn-vision-pose" onclick="setVision('pose')" class="ghost">Pose</button>
        <button id="btn-vision-both" onclick="setVision('both')" class="ghost">Face + Pose</button>
      </div>'''

if old_block in h:
    h = h.replace(old_block, new_block)
else:
    # Fallback individual replacements
    h = h.replace('<button onclick="setCamera(\'rgb\')" class="primary">RGB</button>', '<button class="primary selected">Combined RGB + Depth</button>')
    h = h.replace('<button onclick="setCamera(\'depth\')" class="ghost">Depth</button>', '')
    h = h.replace('<button onclick="setVision(\'off\')" class="ghost">Vision Off</button>', '<button id="btn-vision-off" onclick="setVision(\'off\')" class="ghost selected">Vision Off</button>')
    h = h.replace('<button onclick="setVision(\'face\')" class="ghost">Face</button>', '<button id="btn-vision-face" onclick="setVision(\'face\')" class="ghost">Face</button>')
    h = h.replace('<button onclick="setVision(\'pose\')" class="ghost">Pose</button>', '<button id="btn-vision-pose" onclick="setVision(\'pose\')" class="ghost">Pose</button>')
    h = h.replace('<button onclick="setVision(\'both\')" class="ghost">Face + Pose</button>', '<button id="btn-vision-both" onclick="setVision(\'both\')" class="ghost">Face + Pose</button>')

old_js = '''async function setCamera(mode) {
  await post('/api/set_camera', {mode});
  document.getElementById('camera').src = '/video_feed?t=' + Date.now();
}
async function setVision(mode) { await post('/api/set_vision', {mode}); }'''

new_js = '''function setSelected(prefix, activeName, names) {
  names.forEach(name => {
    const el = document.getElementById(prefix + name);
    if (!el) return;
    if (name === activeName) el.classList.add('selected');
    else el.classList.remove('selected');
  });
}

async function setCamera(mode) {
  await post('/api/set_camera', {mode});
  document.getElementById('camera').src = '/video_feed?t=' + Date.now();
}

async function setVision(mode) {
  await post('/api/set_vision', {mode});
  setSelected('btn-vision-', mode, ['off', 'face', 'pose', 'both']);
}'''

if old_js in h:
    h = h.replace(old_js, new_js)

html_path.write_text(h)

print("Final console patched: combined camera only + MediaPipe on RGB half + lower listen threshold.")
