#!/usr/bin/env bash
echo "===== 1. Locate app.py files ====="
find / -iname "app.py" 2>/dev/null

echo
echo "===== 2. Locate haarcascade xml files ====="
find / -iname "haarcascade_frontalface_default.xml" 2>/dev/null

echo
echo "===== 3. start_final_console_v3.sh contents ====="
find / -iname "start_final_console_v3.sh" 2>/dev/null -exec cat {} \;

echo
echo "===== 4. Is the app currently running? ====="
ps aux | grep -i "python3 app" | grep -v grep

echo
echo "===== 5. What port is it listening on? ====="
ss -tlnp 2>/dev/null | grep python3

echo
echo "===== 6. Try the toggle endpoint (adjust port if step 5 shows different) ====="
for port in 5010 5014; do
  echo "--- trying port $port ---"
  curl -s -m 3 -X POST "http://127.0.0.1:${port}/api/set_face_detection" \
    -H "Content-Type: application/json" -d '{"enabled": true}'
  echo
done

echo
echo "===== 7. Recent app logs (last 30 lines, if found) ====="
find / -iname "*.log" -path "*final_console*" 2>/dev/null -exec tail -n 30 {} \;
