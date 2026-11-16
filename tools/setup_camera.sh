#!/usr/bin/env bash
# Install the WebRTC camera-capture dependencies into .camera_libs/.
#
# Only needed for the `--webrtc` fallback. The default DDS path uses the
# already-installed unitree_sdk2py and needs none of this.
#
# The ROS image and the backend are not touched. This is host-side, per-repo,
# and needs no sudo. Audio dependencies (PortAudio/PyAudio) are deliberately
# omitted; capture_camera.py stubs the unused audio import.
set -euo pipefail

cd "$(dirname "$0")/.." || exit 1

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "$PYTHON not found" >&2
  exit 127
fi
if ! "$PYTHON" -c 'import cv2' >/dev/null 2>&1; then
  echo "OpenCV is required: sudo apt install python3-opencv" >&2
  echo "  (or pip install opencv-python, then rerun)" >&2
  exit 1
fi

echo "installing WebRTC camera dependencies into .camera_libs/ ..."
"$PYTHON" -m pip install --upgrade --target .camera_libs \
  'aiortc>=1.9.0' pycryptodome requests curl_cffi wasmtime lz4 packaging numpy

# --no-deps: the package declares audio extras (PyAudio) that fail to build on
# headless machines. Video and WebRTC do not need them.
"$PYTHON" -m pip install --upgrade --target .camera_libs --no-deps \
  unitree_webrtc_connect

echo
echo "checking import..."
"$PYTHON" - <<'PY'
import sys
import types
sys.path.insert(0, ".camera_libs")
try:
    import sounddevice  # noqa: F401
except Exception:
    sys.modules["sounddevice"] = types.ModuleType("sounddevice")
import unitree_webrtc_connect  # noqa: F401
print("camera dependencies OK")
PY

echo
echo "next:"
echo "  ./tools/capture_camera.py --iface enp7s0 --out bags/camera_baseline"
echo "  ./tools/capture_camera.py --webrtc --probe --ip <robot-ip>"
echo "  (webrtc fw >= 1.1.15: ./tools/fetch_aes_key.sh --email <you> --device-type Go2)"
