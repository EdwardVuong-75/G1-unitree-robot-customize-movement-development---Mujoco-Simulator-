from pathlib import Path

app_path = Path("app_final_console.py")
html_path = Path("templates/final_console.html")

s = app_path.read_text()

# ------------------------------------------------------------
# 1. Add camera locks/helpers after global camera state variables.
# ------------------------------------------------------------
anchor = 'vision_mode = "off"  # off, face, pose, both'
insert = '''vision_mode = "off"  # off, face, pose, both

camera_resource_lock = threading.Lock()
v4l2_auto_rgb_id = None
'''

if "camera_resource_lock = threading.Lock()" not in s:
    s = s.replace(anchor, insert)

# ------------------------------------------------------------
# 2. Add V4L2 auto-find helper before gen_rgb.
# ------------------------------------------------------------
helper = r'''
def find_working_v4l2_rgb():
    """
    Find a usable RGB camera node automatically.
    Old versions used camera id 3, but that is not always stable.
    This function tries the configured RGB_CAMERA_ID first, then scans 0-9.
    """
    global v4l2_auto_rgb_id

    if v4l2_auto_rgb_id is not None:
        return v4l2_auto_rgb_id

    candidates = [RGB_CAMERA_ID] + [i for i in range(10) if i != RGB_CAMERA_ID]

    for cam_id in candidates:
        cap = cv2.VideoCapture(cam_id, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap.release()
            continue

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        ok, frame = cap.read()
        cap.release()

        if ok and frame is not None:
            # Avoid tiny/empty meta streams.
            h, w = frame.shape[:2]
            if w >= 320 and h >= 240:
                v4l2_auto_rgb_id = cam_id
                add_log(f"Auto selected V4L2 RGB camera: /dev/video{cam_id}, shape={frame.shape}")
                return cam_id

    add_log("No usable V4L2 RGB camera found.")
    return None

'''

if "def find_working_v4l2_rgb():" not in s:
    s = s.replace("\ndef gen_rgb():", "\n" + helper + "\ndef gen_rgb():")

# ------------------------------------------------------------
# 3. Replace gen_rgb with V4L2-first auto camera.
# ------------------------------------------------------------
start = s.index("def gen_rgb():")
end = s.index("\ndef gen_depth", start)

new_gen_rgb = r'''def gen_rgb():
    """
    RGB mode:
    Use V4L2 auto-selected RGB camera first.
    This avoids repeatedly opening RealSense RGB and conflicting with Unitree App / RealSense services.
    """
    cam_id = find_working_v4l2_rgb()

    if cam_id is None:
        img = camera_blank("No usable RGB camera found. Try closing phone app camera or camera services.")
        jpg = encode_jpg(img)
        while True:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
            time.sleep(0.3)

    cap = cv2.VideoCapture(cam_id, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 15)

    if not cap.isOpened():
        img = camera_blank(f"RGB /dev/video{cam_id} not available")
        jpg = encode_jpg(img)
        while True:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
            time.sleep(0.3)

    try:
        while True:
            ok, frame = cap.read()

            if not ok or frame is None:
                frame = camera_blank(f"No frame from /dev/video{cam_id}")
            else:
                frame = cv2.resize(frame, (960, 540))
                frame = apply_vision(frame)
                cv2.putText(
                    frame,
                    f"RGB /dev/video{cam_id}",
                    (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.85,
                    (255, 255, 255),
                    2
                )

            jpg = encode_jpg(frame)
            if jpg:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"

            time.sleep(0.05)

    finally:
        cap.release()
'''

s = s[:start] + new_gen_rgb + s[end:]

# ------------------------------------------------------------
# 4. Replace gen_depth with lock-protected RealSense depth.
# ------------------------------------------------------------
start = s.index("def gen_depth():")
end = s.index("\ndef run_sequence_worker", start)

new_gen_depth = r'''def gen_depth():
    """
    Depth mode:
    Use RealSense SDK, but protect the device with a lock.
    If phone App or another camera service is using RealSense, this may still fail.
    """
    if not HAS_REALSENSE:
        img = camera_blank("pyrealsense2 not installed. Depth mode unavailable.")
        jpg = encode_jpg(img)
        while True:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
            time.sleep(0.3)

    acquired = camera_resource_lock.acquire(blocking=False)

    if not acquired:
        img = camera_blank("Depth camera already in use. Close other camera streams and reload.")
        jpg = encode_jpg(img)
        while True:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
            time.sleep(0.3)

    pipeline = rs.pipeline()
    config = rs.config()

    try:
        add_log("Starting RealSense Depth stream.")
        config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
        pipeline.start(config)

        while True:
            frames = pipeline.wait_for_frames(timeout_ms=5000)
            depth_frame = frames.get_depth_frame()

            if not depth_frame:
                frame = camera_blank("No depth frame")
            else:
                depth = np.asanyarray(depth_frame.get_data())
                frame = cv2.applyColorMap(
                    cv2.convertScaleAbs(depth, alpha=0.03),
                    cv2.COLORMAP_JET
                )
                frame = cv2.resize(frame, (960, 540))
                cv2.putText(
                    frame,
                    "RealSense Depth",
                    (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.85,
                    (255, 255, 255),
                    2
                )

            jpg = encode_jpg(frame)
            if jpg:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"

            time.sleep(0.05)

    except Exception as e:
        add_log(f"Depth camera error: {e}")
        msg = "Depth camera error. Close Unitree App camera / other camera services, then reload. " + str(e)
        img = camera_blank(msg)
        jpg = encode_jpg(img)
        while True:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
            time.sleep(0.3)

    finally:
        try:
            pipeline.stop()
        except Exception:
            pass
        try:
            camera_resource_lock.release()
        except Exception:
            pass
'''

s = s[:start] + new_gen_depth + s[end:]

# ------------------------------------------------------------
# 5. Reset auto camera id when set_camera rgb is clicked.
# ------------------------------------------------------------
if "global camera_mode, v4l2_auto_rgb_id" not in s:
    s = s.replace(
        'def api_set_camera():\n    global camera_mode',
        'def api_set_camera():\n    global camera_mode, v4l2_auto_rgb_id'
    )
    s = s.replace(
        'camera_mode = mode\n    add_log(f"Camera mode set to {mode}")',
        'camera_mode = mode\n    if mode == "rgb":\n        v4l2_auto_rgb_id = None\n    add_log(f"Camera mode set to {mode}")'
    )

app_path.write_text(s)

# ------------------------------------------------------------
# HTML patch for active button states.
# ------------------------------------------------------------
h = html_path.read_text()

# Add selected button style
if "button.selected" not in h:
    h = h.replace(
        "button.active { background: #ef4444; border-color: #ef4444; }",
        "button.active { background: #ef4444; border-color: #ef4444; }\nbutton.selected { background: #e5e7eb; color:#0b0d10; border-color:#e5e7eb; }"
    )

# Add button ids for camera buttons
h = h.replace(
    '<button onclick="setCamera(\'rgb\')" class="primary">RGB</button>',
    '<button id="btn-camera-rgb" onclick="setCamera(\'rgb\')" class="primary selected">RGB</button>'
)
h = h.replace(
    '<button onclick="setCamera(\'depth\')" class="ghost">Depth</button>',
    '<button id="btn-camera-depth" onclick="setCamera(\'depth\')" class="ghost">Depth</button>'
)

# Add button ids for vision buttons
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

# Replace JS setCamera/setVision functions with active-state versions.
old = '''async function setCamera(mode) {
  await post('/api/set_camera', {mode});
  document.getElementById('camera').src = '/video_feed?t=' + Date.now();
}
async function setVision(mode) { await post('/api/set_vision', {mode}); }'''

new = '''function setSelected(prefix, activeName, names) {
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

if old in h:
    h = h.replace(old, new)
else:
    print("WARNING: setCamera/setVision JS block not found exactly; please check HTML manually.")

html_path.write_text(h)

print("Camera and UI active-state patch completed.")
