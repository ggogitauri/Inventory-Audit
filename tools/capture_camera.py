#!/usr/bin/env python3
"""Capture JPEG stills from the Go2 front camera.

Primary path (no account, no WebRTC): the robot exposes the camera through a
DDS RPC service ("videohub"). `unitree_sdk2py`'s `VideoClient.GetImageSample`
returns one 1920x1080 JPEG per request, so this works whenever DDS can see the
robot (the same link the ROS nodes use):

    ./tools/capture_camera.py --iface enp7s0 --out bags/camera_baseline

Fallback path (`--webrtc`): the same channel the Unitree app uses, via
`unitree_webrtc_connect`. Useful off-LAN (`--remote`) or when DDS is blocked.
Firmware >= 1.1.15 requires the per-device AES key from your Unitree account
(see `tools/fetch_aes_key.sh`); older firmware does not.

    ./tools/capture_camera.py --webrtc --ip 192.168.123.161

If you are on the robot's own WiFi AP instead of Ethernet, use
`--webrtc --ap` (robot at 192.168.12.1). To check LAN reachability first:

    ./tools/capture_camera.py --probe --ip 192.168.123.161

Drive/pose the robot, then Ctrl-C. The output directory holds frame_*.jpg and
metadata.json; copy it with the bags.

Both paths are read-only with respect to motion: they request camera frames and
never publish sport/control commands. Close the Unitree app before using the
WebRTC fallback; the robot accepts one WebRTC controller at a time.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import time
from pathlib import Path

# The camera dependencies live in a repo-local directory so they do not fight
# with the ROS/container Python. setup_camera.sh populates it.
_REPO = Path(__file__).resolve().parent.parent
_LIBS = _REPO / ".camera_libs"
if _LIBS.is_dir():
    sys.path.insert(0, str(_LIBS))

# Headless machines often have no PortAudio. The driver imports sounddevice at
# module import time for its optional audio channel; video does not need it.
try:  # pragma: no cover - depends on host audio libraries
    import sounddevice  # noqa: F401
except Exception:
    import types

    sys.modules["sounddevice"] = types.ModuleType("sounddevice")

try:
    import cv2
except ImportError as exc:  # pragma: no cover - setup problem
    raise SystemExit(
        "OpenCV is required (python3-opencv or pip install opencv-python): "
        f"{exc}"
    )


# LAN signaling ports probed by unitree_webrtc_connect, in its order.
SIGNALING_PORTS = (9991, 8081)

# Where `unitree-fetch-aes-key --quiet` output can be cached (chmod 600).
DEFAULT_AES_KEY_FILE = "~/.unitree_go2_aes_key"


def _load_aes_key(cli_value: str) -> str:
    """CLI/env key wins; otherwise read the cached key file."""
    if cli_value:
        return cli_value.strip()
    path = Path(os.environ.get("UNITREE_AES_KEY_FILE",
                               DEFAULT_AES_KEY_FILE)).expanduser()
    if path.is_file():
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return ""



def _signaling_hint(ip: str) -> str:
    """Actionable text for the most common 'robot not reachable' failure."""
    return (
        f"No LAN signaling port answered on {ip}.\n"
        "  1. 192.168.123.x is the robot's internal/DDS address. It can answer\n"
        "     ping through a phone hotspot without accepting WebRTC from this\n"
        "     laptop. Read the real address in the app:\n"
        "     Device -> Data -> STA Network -> wlan0, then pass that as --ip.\n"
        "  2. Close the Unitree app first; the robot accepts one WebRTC client.\n"
        "  3. Or join the robot's own WiFi AP and run with --ap (192.168.12.1).\n"
        "  4. Robot on another network? Use --remote --serial ... --username ...\n"
        "Then retry, e.g. ./tools/capture_camera.py --probe --ip <robot-ip>"
    )


def _probe(ip: str) -> int:
    """Test the LAN signaling ports and explain the result; no WebRTC."""
    import socket

    print(f"probing {ip} for LAN signaling ports {SIGNALING_PORTS}...")
    reachable = []
    for port in SIGNALING_PORTS:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.5)
        try:
            code = sock.connect_ex((ip, port))
        finally:
            sock.close()
        if code == 0:
            reachable.append(port)
            print(f"  {ip}:{port}  open")
        else:
            print(f"  {ip}:{port}  closed/unreachable (errno {code})")
    if reachable:
        print(f"OK: {ip} exposes {reachable}; retry the capture.")
        return 0
    print(_signaling_hint(ip))
    return 1


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Capture Go2 front-camera JPEGs (DDS by default)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ./tools/capture_camera.py --iface enp7s0 --out bags/camera_baseline\n"
            "  ./tools/capture_camera.py --webrtc --ip 192.168.123.161 --aes-key <32-hex>\n"
            "  ./tools/capture_camera.py --webrtc --probe --ip 192.168.123.161\n"
        ),
    )
    p.add_argument("--iface", default=os.environ.get("GO2_IFACE", ""),
                   help="DDS network interface, e.g. enp7s0 (default: auto)")
    p.add_argument("--dds-timeout", type=float, default=3.0,
                   help="DDS RPC timeout in seconds (default 3.0)")
    p.add_argument("--webrtc", action="store_true",
                   help="use the WebRTC video channel instead of DDS")
    p.add_argument("--ip", help="robot IP on the local network (STA mode)")
    p.add_argument("--serial", help="robot serial number (uses multicast discovery)")
    p.add_argument("--ap", action="store_true",
                   help="connect to the robot's own WiFi AP (192.168.12.1)")
    p.add_argument("--remote", action="store_true",
                   help="use the Unitree cloud/TURN relay (needs --serial, "
                        "--username and --password)")
    p.add_argument("--username", default=os.environ.get("UNITREE_EMAIL", ""),
                   help="Unitree account email for --remote")
    p.add_argument("--password", default=os.environ.get("UNITREE_PASSWORD", ""),
                   help="Unitree account password for --remote "
                        "(prompted if omitted)")
    p.add_argument("--probe", action="store_true",
                   help="only test TCP 9991/8081 on --ip and exit")
    p.add_argument("--aes-key", default=os.environ.get("UNITREE_AES_KEY", ""),
                   help="32-hex per-device key (fw >= 1.1.15); "
                        "also read from UNITREE_AES_KEY, then "
                        f"{DEFAULT_AES_KEY_FILE}")
    p.add_argument("--out", default="", help="output directory "
                                              "(default: bags/camera_<stamp>)")
    p.add_argument("--interval", type=float, default=1.0,
                   help="seconds between saved frames (default 1.0)")
    p.add_argument("--duration", type=float, default=0.0,
                   help="stop after N seconds; 0 = until Ctrl-C")
    p.add_argument("--max-frames", type=int, default=0,
                   help="stop after N saved frames; 0 = unlimited")
    p.add_argument("--jpeg-quality", type=int, default=92,
                   help="JPEG quality 1-100 (default 92)")
    p.add_argument("--discover", action="store_true",
                   help="print discoverable Go2 serial/IP pairs and exit")
    p.add_argument("--region", choices=("global", "cn"), default="global")
    p.add_argument("--device-type", default="Go2",
                   choices=("Go2", "G1", "R1"))
    return p.parse_args(argv)


def _discover(args: argparse.Namespace) -> int:
    from unitree_webrtc_connect import discover_ip_sn

    print("Discovering Unitree devices on the local network...")
    found = discover_ip_sn(timeout=4, device_type=args.device_type)
    if not found:
        print("No device answered. Check the laptop is on the robot's subnet, "
              "then pass --ip from the Unitree app "
              "(Device -> Data -> STA Network: wlan0).")
        return 1
    for serial, ip in sorted(found.items()):
        print(f"{serial}  {ip}")
    return 0


async def _capture(args: argparse.Namespace, out: Path) -> int:
    from aiortc.mediastreams import MediaStreamError
    from unitree_webrtc_connect import (
        AesKeyRejectedError,
        AesKeyRequiredError,
        LocalSignalingPortError,
        RobotBusyError,
        UnitreeWebRTCConnection,
        WebRTCConnectionMethod,
    )

    out.mkdir(parents=True, exist_ok=True)
    aes_key = _load_aes_key(args.aes_key)
    if args.remote:
        method = WebRTCConnectionMethod.Remote
    elif args.ap:
        method = WebRTCConnectionMethod.LocalAP
    else:
        method = WebRTCConnectionMethod.LocalSTA
    meta = {
        "started_at": time.time(),
        "started_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "ip": args.ip or "",
        "serial": args.serial or "",
        "connection_method": method.name,
        "interval_s": args.interval,
        "duration_s": args.duration,
        "jpeg_quality": args.jpeg_quality,
        "frames": 0,
        "resolution": None,
        "source": "unitree_webrtc_connect video channel",
    }
    saved = 0
    last_save = 0.0
    stop = asyncio.Event()

    async def on_track(track) -> None:
        nonlocal saved, last_save
        while not stop.is_set():
            try:
                frame = await track.recv()
            except MediaStreamError:
                break
            except asyncio.CancelledError:
                break
            now = time.time()
            if now - last_save < max(0.0, args.interval):
                continue
            img = frame.to_ndarray(format="bgr24")
            if meta["resolution"] is None:
                meta["resolution"] = [int(img.shape[1]), int(img.shape[0])]
                print(f"video: {img.shape[1]}x{img.shape[0]} "
                      f"({frame.format.name})")
            ok, buf = cv2.imencode(
                ".jpg", img,
                [int(cv2.IMWRITE_JPEG_QUALITY), max(1, min(100, args.jpeg_quality))],
            )
            if not ok:
                print("warning: JPEG encode failed; skipping frame", file=sys.stderr)
                continue
            last_save = now
            saved += 1
            name = f"frame_{saved:06d}_{int(now * 1000)}.jpg"
            (out / name).write_bytes(buf.tobytes())
            if saved == 1 or saved % 10 == 0:
                print(f"saved {saved} frame(s); latest {name}")
            if args.max_frames > 0 and saved >= args.max_frames:
                stop.set()
                break

    conn = UnitreeWebRTCConnection(
        method,
        ip=args.ip or None,
        serialNumber=args.serial or None,
        username=args.username or None,
        password=args.password or None,
        aes_128_key=aes_key or None,
        region=args.region,
        device_type=args.device_type,
    )
    if args.remote:
        target = f"cloud relay (serial {args.serial})"
    elif args.ap:
        target = "robot AP (192.168.12.1)"
    else:
        target = args.ip or args.serial or "discovered device"
    print(f"connecting to {target}...")
    try:
        await conn.connect()
    except RobotBusyError:
        print("robot is busy: close the Unitree app or the other WebRTC client "
              "and retry.", file=sys.stderr)
        return 2
    except AesKeyRequiredError:
        print("this firmware needs --aes-key (32 hex chars). Get it with "
              "`unitree-fetch-aes-key --email ...` or from the app.", file=sys.stderr)
        return 2
    except AesKeyRejectedError:
        print("the --aes-key was rejected; check the 32-hex value.", file=sys.stderr)
        return 2
    except LocalSignalingPortError:
        print(_signaling_hint(args.ip or "192.168.12.1"), file=sys.stderr)
        return 3

    print("connected; starting video channel (read-only, no motion commands)")
    conn.video.switchVideoChannel(True)
    conn.video.add_track_callback(on_track)

    deadline = (time.monotonic() + args.duration) if args.duration > 0 else None
    try:
        while not stop.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.2)
    finally:
        stop.set()
        with contextlib.suppress(Exception):
            await conn.disconnect()
        meta["ended_at"] = time.time()
        meta["duration_recorded_s"] = round(meta["ended_at"] - meta["started_at"], 2)
        meta["frames"] = saved
        (out / "metadata.json").write_text(
            json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        print(f"captured {saved} frame(s) in {out}")
    return 0


def _capture_dds(args: argparse.Namespace, out: Path) -> int:
    """Capture JPEGs through the robot's DDS RPC camera service."""
    import numpy as np

    try:
        from unitree_sdk2py.core.channel import ChannelFactoryInitialize
        from unitree_sdk2py.go2.video.video_client import VideoClient
    except ImportError as exc:  # pragma: no cover - setup problem
        raise SystemExit(
            "unitree_sdk2py is required for DDS capture "
            "(pip install unitree_sdk2py): " + str(exc)
        )

    out.mkdir(parents=True, exist_ok=True)
    meta = {
        "started_at": time.time(),
        "started_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "connection_method": "DDS-RPC",
        "iface": args.iface or "auto",
        "interval_s": args.interval,
        "duration_s": args.duration,
        "frames": 0,
        "resolution": None,
        "source": "unitree_sdk2py VideoClient.GetImageSample()",
    }

    if args.iface:
        ChannelFactoryInitialize(0, args.iface)
    else:
        ChannelFactoryInitialize(0)
    client = VideoClient()
    client.SetTimeout(max(0.5, args.dds_timeout))
    client.Init()

    print("camera service ready; capturing (read-only, no motion commands)")
    saved = 0
    bad = 0
    last_error = 0
    next_at = 0.0
    deadline = (time.monotonic() + args.duration) if args.duration > 0 else None
    try:
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                break
            if args.max_frames > 0 and saved >= args.max_frames:
                break
            now = time.monotonic()
            if now < next_at:
                time.sleep(min(next_at - now, 0.2))
                continue

            code, data = client.GetImageSample()
            if code != 0:
                last_error = code
                if saved == 0:
                    import socket

                    ifaces = [n for _, n in socket.if_nameindex() if n != "lo"]
                    print(
                        f"GetImageSample failed (code {code}).\n"
                        "  Check the robot is reachable over DDS and pass the\n"
                        f"  right interface, e.g. --iface enp7s0.\n"
                        f"  interfaces here: {', '.join(ifaces)}",
                        file=sys.stderr,
                    )
                    return 3
                print(f"GetImageSample failed (code {code}); stopping",
                      file=sys.stderr)
                break

            buf = bytes(data)
            if (len(buf) < 4 or buf[0] != 0xFF or buf[1] != 0xD8
                    or buf[-2:] != b"\xff\xd9"):
                # The service sometimes returns a partial frame on the first
                # request after Init; the next call is complete.
                bad += 1
                if saved == 0 and bad >= 5:
                    print("camera keeps returning incomplete JPEGs; "
                          "check the videohub service", file=sys.stderr)
                    return 3
                print("warning: incomplete JPEG sample; retrying",
                      file=sys.stderr)
                next_at = time.monotonic() + 0.1
                continue
            bad = 0

            if meta["resolution"] is None:
                img = cv2.imdecode(np.frombuffer(buf, dtype=np.uint8),
                                   cv2.IMREAD_COLOR)
                if img is not None:
                    meta["resolution"] = [int(img.shape[1]), int(img.shape[0])]
                    print(f"camera: {img.shape[1]}x{img.shape[0]} JPEG")

            now = time.time()
            saved += 1
            name = f"frame_{saved:06d}_{int(now * 1000)}.jpg"
            (out / name).write_bytes(buf)
            if saved == 1 or saved % 10 == 0:
                print(f"saved {saved} frame(s); latest {name}")
            next_at = time.monotonic() + max(0.0, args.interval)
    finally:
        meta["ended_at"] = time.time()
        meta["duration_recorded_s"] = round(meta["ended_at"] - meta["started_at"], 2)
        meta["frames"] = saved
        if last_error:
            meta["last_error_code"] = last_error
        (out / "metadata.json").write_text(
            json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        print(f"captured {saved} frame(s) in {out}")
    return 0


def _free_out_dir(out: Path) -> Path:
    """Pick an unused directory; never mix runs or overwrite metadata.json."""
    def empty(p: Path) -> bool:
        return p.is_dir() and not any(p.iterdir())

    if not out.exists() or empty(out):
        return out
    n = 2
    while True:
        cand = out.with_name(f"{out.name}_{n}")
        if not cand.exists() or empty(cand):
            return cand
        n += 1


def main(argv=None) -> int:
    args = _parse_args(argv)
    if args.discover:
        return _discover(args)
    if args.probe:
        ip = args.ip or ("192.168.12.1" if args.ap else "")
        if not ip:
            print("--probe needs --ip (or --ap for 192.168.12.1)", file=sys.stderr)
            return 2
        return _probe(ip)
    if not args.webrtc and (args.remote or args.ap or args.ip
                            or args.serial or args.aes_key):
        print("note: --ip/--serial/--ap/--remote/--aes-key only apply to "
              "--webrtc; using DDS", file=sys.stderr)
    if args.webrtc:
        if args.remote:
            if not args.serial:
                print("--remote needs --serial (shown in the Unitree app)",
                      file=sys.stderr)
                return 2
            if not args.username:
                print("--remote needs --username (your Unitree account email)",
                      file=sys.stderr)
                return 2
            if not args.password:
                import getpass

                args.password = getpass.getpass(
                    f"Unitree password for {args.username}: ")
                if not args.password:
                    print("no password given; aborting", file=sys.stderr)
                    return 2
        elif not args.ap and not args.ip and not args.serial:
            print("--webrtc needs --ip (from the Unitree app), --serial, --ap, "
                  "or --remote", file=sys.stderr)
            return 2
    if args.interval < 0:
        print("--interval must be >= 0", file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else (
        _REPO / "bags" / f"camera_{time.strftime('%Y%m%d-%H%M%S')}")
    free = _free_out_dir(out)
    if free != out:
        print(f"note: {out} already exists; writing to {free}", file=sys.stderr)
        out = free
    try:
        if args.webrtc:
            return asyncio.run(_capture(args, out))
        return _capture_dds(args, out)
    except KeyboardInterrupt:
        print("\ninterrupted; partial frames and metadata.json are kept")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
