from pathlib import Path

app_path = Path("app_final_console.py")
html_path = Path("templates/final_console.html")

s = app_path.read_text()

# ------------------------------------------------------------
# Replace gen_rgb: use fixed V4L2 RGB_CAMERA_ID, not auto-first-camera.
# ------------------------------------------------------------
start = s.index("def gen_rgb():")
end = s.index("\ndef gen_depth", start)

new_gen_rgb = r'''def gen_rgb():
    """
    RGB mode:
    Use the known color V4L2 camera ID discovered by probe_color_camera.py.
    This avoids selecting RealSense IR grayscale nodes.
    MediaPipe Face/Pose is applied only here.
    """
    cam_id = RGB_CAMERA_ID

    cap = cv2.VideoCapture(cam_id, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 15)

    if not cap.isOpened():
        img = camera_blank(f"RGB /dev/video{cam_id} not available. Run probe_color_camera.py.")
        jpg = encode_jpg(img)
        while True:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
            time.sleep(0.3)

    try:
        while True:
            ok, frame = cap.read()

            if not ok or frame is None:
                frame = camera_blank(f"No frame from RGB /dev/video{cam_id}")
            else:
                # If a grayscale frame somehow appears, convert but warn.
                if len(frame.shape) == 2:
                    frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
                    cv2.putText(
                        frame,
                        "WARNING: grayscale camera selected",
                        (20, 85),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (0, 0, 255),
                        2
                    )

                frame = cv2.resize(frame, (960, 540))

                try:
                    frame = apply_vision(frame)
                except Exception as e:
                    add_log(f"MediaPipe apply_vision error: {e}")
                    cv2.putText(
                        frame,
                        "MediaPipe error",
                        (20, 85),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (0, 0, 255),
                        2
                    )

                frame = draw_camera_zones(frame, f"RGB /dev/video{cam_id}")

            jpg = encode_jpg(frame)
            if jpg:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"

            time.sleep(0.05)

    finally:
        cap.release()
'''

s = s[:start] + new_gen_rgb + s[end:]

# ------------------------------------------------------------
# Ensure draw_camera_zones exists.
# ------------------------------------------------------------
if "def draw_camera_zones(frame, label):" not in s:
    insert_after = "def camera_blank(text, width=960, height=540):"
    idx = s.index(insert_after)
    # Insert before camera_blank for readability
    helper = r'''
def draw_camera_zones(frame, label):
    h, w = frame.shape[:2]
    x1 = w // 3
    x2 = 2 * w // 3

    cv2.line(frame, (x1, 0), (x1, h), (255, 255, 255), 2)
    cv2.line(frame, (x2, 0), (x2, h), (255, 255, 255), 2)

    cv2.putText(frame, "LEFT", (20, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    cv2.putText(frame, "CENTER", (x1 + 20, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    cv2.putText(frame, "RIGHT", (x2 + 20, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)

    cv2.putText(frame, label, (20, h - 24), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2)
    return frame


'''
    s = s[:idx] + helper + s[idx:]

# ------------------------------------------------------------
# Patch gen_depth to draw the same zones.
# ------------------------------------------------------------
if "draw_camera_zones(frame, \"RealSense Depth\")" not in s:
    s = s.replace(
        '''cv2.putText(frame, "Depth", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (255, 255, 255), 2)''',
        '''frame = draw_camera_zones(frame, "RealSense Depth")'''
    )

app_path.write_text(s)

# ------------------------------------------------------------
# HTML button active states.
# ------------------------------------------------------------
h = html_path.read_text()

if "button.selected" not in h:
    h = h.replace(
        "button.active { background: #ef4444; border-color: #ef4444; }",
        "button.active { background: #ef4444; border-color: #ef4444; }\nbutton.selected { background: #e5e7eb; color:#0b0d10; border-color:#e5e7eb; }"
    )

h = h.replace(
    '<button onclick="setCamera(\'rgb\')" class="primary">RGB</button>',
    '<button id="btn-camera-rgb" onclick="setCamera(\'rgb\')" class="primary selected">RGB</button>'
)

h = h.replace(
    '<button onclick="setCamera(\'depth\')" class="ghost">Depth</button>',
    '<button id="btn-camera-depth" onclick="setCamera(\'depth\')" class="ghost">Depth</button>'
)

h = h.replace(
    '<button onclick="setVision(\'off\')" class="ghost">Vision Off</button>',
    '<button id="btn-vision-off" onclick="setVision(\'off\')" class="ghost selected">Vision Off</button>'
)

h = h.replace(
    '<button onclick="setVision(\'face\')" class="ghost">Face</button>',
    '<button id="btn-vision-face" onclick="setVision(\'face\')" class="ghost">Face</button>'
)

h = h.replace(
    '<button onclick="setVision(\'pose\')" class="ghost">Pose</button>',
    '<button id="btn-vision-pose" onclick="setVision(\'pose\')" class="ghost">Pose</button>'
)

h = h.replace(
    '<button onclick="setVision(\'both\')" class="ghost">Face + Pose</button>',
    '<button id="btn-vision-both" onclick="setVision(\'both\')" class="ghost">Face + Pose</button>'
)

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
  setSelected('btn-camera-', mode, ['rgb', 'depth']);
  document.getElementById('camera').src = '/video_feed?t=' + Date.now();
}

async function setVision(mode) {
  await post('/api/set_vision', {mode});
  setSelected('btn-vision-', mode, ['off', 'face', 'pose', 'both']);
}'''

if old_js in h:
    h = h.replace(old_js, new_js)

html_path.write_text(h)

print("Patched final console to use fixed color RGB_CAMERA_ID and MediaPipe on RGB.")
