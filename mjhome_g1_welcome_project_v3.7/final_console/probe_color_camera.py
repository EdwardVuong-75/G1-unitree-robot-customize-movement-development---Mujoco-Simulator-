import cv2
import numpy as np
from pathlib import Path

out_dir = Path("/tmp/final_console_camera_probe")
out_dir.mkdir(exist_ok=True)

scores = []

print("Probing /dev/video0 - /dev/video9")
print("Images will be saved to:", out_dir)

for i in range(10):
    cap = cv2.VideoCapture(i, cv2.CAP_V4L2)

    if not cap.isOpened():
        print(f"/dev/video{i}: cannot open")
        continue

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 15)

    frame = None
    ok = False

    # Read a few frames to avoid startup black frames.
    for _ in range(10):
        ok, frame = cap.read()
        if ok and frame is not None:
            break

    cap.release()

    if not ok or frame is None:
        print(f"/dev/video{i}: no frame")
        continue

    h, w = frame.shape[:2]

    if len(frame.shape) == 2:
        color_score = 0.0
        brightness = float(np.mean(frame))
        saved = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    else:
        b, g, r = cv2.split(frame)
        # 彩色画面：三个通道差异大；灰度/IR：三个通道几乎一样
        color_score = float(np.mean(np.abs(b.astype(np.float32) - g.astype(np.float32))) +
                            np.mean(np.abs(g.astype(np.float32) - r.astype(np.float32))) +
                            np.mean(np.abs(b.astype(np.float32) - r.astype(np.float32))))
        brightness = float(np.mean(frame))
        saved = frame

    path = out_dir / f"video{i}_score_{color_score:.1f}_brightness_{brightness:.1f}.jpg"
    cv2.imwrite(str(path), saved)

    print(f"/dev/video{i}: OK shape={frame.shape}, color_score={color_score:.1f}, brightness={brightness:.1f}, saved={path}")

    # 过滤掉太暗、太小、灰度相机
    usable = (w >= 320 and h >= 240 and brightness > 10)
    scores.append((color_score, brightness, i, usable))

valid = [x for x in scores if x[3]]
valid.sort(reverse=True)

print()
print("=== Ranked candidates ===")
for color_score, brightness, i, usable in valid:
    print(f"/dev/video{i}: color_score={color_score:.1f}, brightness={brightness:.1f}")

if valid:
    best = valid[0][2]
    print()
    print("BEST_COLOR_CAMERA_ID =", best)
    with open("rgb_camera_id.env", "w") as f:
        f.write(f"export RGB_CAMERA_ID={best}\n")
    print("Saved rgb_camera_id.env")
else:
    print("No usable color camera found.")
