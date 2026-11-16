#!/usr/bin/env bash
# Fetch/cache the per-device AES-128 key used by Go2 firmware >= 1.1.15.
#
# The pip --target install puts the console script in .camera_libs/bin but does
# not add .camera_libs to sys.path; the package also imports the optional
# sounddevice module at import time. This wrapper handles both.
#
#   ./tools/fetch_aes_key.sh --email you@example.com            # list devices
#   ./tools/fetch_aes_key.sh --email you@example.com \
#       --sn B42D... --quiet > ~/.unitree_go2_aes_key            # cache one key
set -euo pipefail

cd "$(dirname "$0")/.." || exit 1

if [[ ! -d .camera_libs/unitree_webrtc_connect ]]; then
  echo "camera deps missing; run ./tools/setup_camera.sh first" >&2
  exit 1
fi

exec "${PYTHON:-python3}" - "$@" <<'PY'
import sys
import types

sys.path.insert(0, ".camera_libs")
try:  # audio is optional; the headless host has no PortAudio
    import sounddevice  # noqa: F401
except Exception:
    sys.modules["sounddevice"] = types.ModuleType("sounddevice")

from unitree_webrtc_connect._cli import main  # noqa: E402

sys.argv = ["unitree-fetch-aes-key", *sys.argv[1:]]
raise SystemExit(main())
PY
