#!/usr/bin/env python3
"""
gSender job recorder
--------------------
Watches gSender (1.6.x) over its local socket.io connection and records the
USB webcam with ffmpeg while a job is running. One video file per job.

  running -> start recording
  paused  -> keep recording (so you see what happened around a pause)
  idle    -> stop recording (after a short grace period)

Configure with environment variables (see the systemd unit), all optional:

  STREAM_URL        record from a ustreamer MJPEG stream instead of opening the
                    camera directly, e.g. http://127.0.0.1:8080/stream
                    (lets a live preview run at the same time)
  GSENDER_URL       e.g. http://127.0.0.1:8000  (default: auto-detect gSender's port)
  VIDEO_DEVICE      default /dev/video0
  VIDEO_SIZE        default 1280x720
  VIDEO_FPS         default 30
  VIDEO_FORMAT      default mjpeg  (camera input format; MJPEG is copied, no re-encode)
  OUTPUT_DIR        default ~/cnc-videos
  KEEP_DAYS         delete recordings older than this; 0 = keep forever (default 14)
  STOP_GRACE_SEC    wait this long in idle before stopping (default 5)
  POST_ROLL_SEC     keep recording this long after the job ends (default 3)
  MAX_MINUTES       safety cap per recording; 0 = none (default 600)
  CAMERA_CONTROLS   v4l2 controls applied before each recording, comma-separated
                    and applied in order, e.g.
                    focus_automatic_continuous=0,focus_absolute=40
  AUDIO_DEVICE      ALSA capture device, e.g. plughw:CARD=C920,DEV=0
                    (empty = no audio, the default)
  AUDIO_BITRATE     default 128k (AAC)
  FFMPEG_INPUT      advanced: replace the whole ffmpeg input section (for testing)
"""

import logging
import os
import re
import shlex
import signal
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import socketio  # pip/apt: python-socketio (client), v5.x talks to socket.io v4

log = logging.getLogger("gsender-recorder")

# ---------------------------------------------------------------- config
def env(name, default):
    v = os.environ.get(name)
    return v if v not in (None, "") else default

GSENDER_URL = env("GSENDER_URL", None)
STREAM_URL = env("STREAM_URL", "")
VIDEO_DEVICE = env("VIDEO_DEVICE", "/dev/video0")
VIDEO_SIZE = env("VIDEO_SIZE", "1280x720")
VIDEO_FPS = env("VIDEO_FPS", "30")
VIDEO_FORMAT = env("VIDEO_FORMAT", "mjpeg")
OUTPUT_DIR = Path(os.path.expanduser(env("OUTPUT_DIR", "~/cnc-videos")))
KEEP_DAYS = float(env("KEEP_DAYS", "14"))
STOP_GRACE_SEC = float(env("STOP_GRACE_SEC", "5"))
POST_ROLL_SEC = float(env("POST_ROLL_SEC", "3"))
MAX_MINUTES = float(env("MAX_MINUTES", "600"))
FFMPEG_INPUT = env("FFMPEG_INPUT", None)
CAMERA_CONTROLS = env("CAMERA_CONTROLS", "")
AUDIO_DEVICE = env("AUDIO_DEVICE", "")
AUDIO_BITRATE = env("AUDIO_BITRATE", "128k")


def apply_camera_controls():
    """Set focus/exposure etc. Applied one at a time, in order, because some
    controls (focus_absolute) are ignored while their auto mode is still on."""
    for ctrl in [c.strip() for c in CAMERA_CONTROLS.split(",") if c.strip()]:
        r = subprocess.run(["v4l2-ctl", "-d", VIDEO_DEVICE, "-c", ctrl],
                           capture_output=True, text=True)
        if r.returncode != 0:
            log.warning("Could not set %s: %s", ctrl, (r.stderr or r.stdout).strip())


# ---------------------------------------------------------------- recorder
class Recorder:
    def __init__(self):
        self.proc = None
        self.path = None
        self.started_at = None
        self.lock = threading.Lock()

    @property
    def active(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, job_name=None):
        with self.lock:
            if self.active:
                return
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            label = f"_{safe_name(job_name)}" if job_name else ""
            self.path = OUTPUT_DIR / f"{stamp}{label}.mkv"

            if not FFMPEG_INPUT:
                apply_camera_controls()
            if FFMPEG_INPUT:
                inp = shlex.split(FFMPEG_INPUT)
                codec = ["-c:v", "libx264", "-preset", "ultrafast"]
            else:
                # Wall-clock timestamps on both inputs keep sound and picture
                # lined up; bigger queues stop drops while ffmpeg starts.
                if STREAM_URL:
                    # ustreamer owns the camera; read its MJPEG stream.
                    # Frames are the camera's own JPEGs, copied untouched.
                    inp = ["-thread_queue_size", "512",
                           "-use_wallclock_as_timestamps", "1",
                           "-f", "mpjpeg", "-reconnect", "1",
                           "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
                           "-i", STREAM_URL]
                    codec = ["-c:v", "copy"]
                else:
                    inp = ["-thread_queue_size", "512",
                           "-use_wallclock_as_timestamps", "1",
                           "-f", "v4l2", "-input_format", VIDEO_FORMAT,
                           "-video_size", VIDEO_SIZE, "-framerate", str(VIDEO_FPS),
                           "-i", VIDEO_DEVICE]
                    # MJPEG from the camera is stored as-is: almost no CPU on the Pi.
                    # For a raw (yuyv422) camera, use hardware H.264 instead.
                    codec = (["-c:v", "copy"] if VIDEO_FORMAT == "mjpeg"
                             else ["-c:v", "h264_v4l2m2m", "-b:v", "4M"])
                if AUDIO_DEVICE:
                    inp += ["-thread_queue_size", "1024",
                            "-use_wallclock_as_timestamps", "1",
                            "-f", "alsa", "-ac", "2", "-i", AUDIO_DEVICE]
                    codec += ["-c:a", "aac", "-b:a", AUDIO_BITRATE,
                              "-af", "aresample=async=1"]
                    codec = ["-map", "0:v", "-map", "1:a"] + codec

            cap = ["-t", str(int(MAX_MINUTES * 60))] if MAX_MINUTES > 0 else []
            cmd = (["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y"]
                   + inp + codec + cap + [str(self.path)])
            log.info("Recording -> %s", self.path)
            log.debug("ffmpeg: %s", " ".join(cmd))
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
            self.started_at = time.time()

    def stop(self):
        with self.lock:
            if not self.active:
                self.proc = None
                return
            log.info("Stopping recording (%.0fs)", time.time() - self.started_at)
            try:
                self.proc.stdin.write(b"q")   # ffmpeg's clean shutdown
                self.proc.stdin.flush()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.send_signal(signal.SIGINT)
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            log.info("Saved %s", self.path)
            self.proc = None
        cleanup_old()


def safe_name(name):
    stem = Path(str(name)).stem
    return re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-")[:60] or "job"


def cleanup_old():
    if KEEP_DAYS <= 0 or not OUTPUT_DIR.exists():
        return
    cutoff = time.time() - KEEP_DAYS * 86400
    for f in OUTPUT_DIR.glob("*.mkv"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                log.info("Deleted old recording %s", f.name)
        except OSError:
            pass


# ---------------------------------------------------------------- gSender discovery
def find_gsender_urls():
    """gSender desktop listens on a random localhost port unless Remote Mode
    is on. Find TCP ports owned by a gSender process."""
    if GSENDER_URL:
        return [GSENDER_URL]
    urls = []
    try:
        out = subprocess.run(["ss", "-ltnpH"], capture_output=True, text=True).stdout
    except FileNotFoundError:
        out = ""
    for line in out.splitlines():
        if "gsender" not in line.lower() and "sender" not in line.lower():
            continue
        m = re.search(r"\s(\S+):(\d+)\s", line)
        if not m:
            continue
        host, port = m.group(1), m.group(2)
        host = "127.0.0.1" if host in ("*", "0.0.0.0", "[::]", "::") else host.strip("[]")
        urls.append(f"http://{host}:{port}")
    return urls


# ---------------------------------------------------------------- main loop
def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    rec = Recorder()
    state = {"workflow": "idle", "job": None, "port": None, "stop_timer": None}

    sio = socketio.Client(reconnection=True, reconnection_delay=2,
                          reconnection_delay_max=10, logger=False)

    def cancel_stop():
        t = state["stop_timer"]
        if t:
            t.cancel()
            state["stop_timer"] = None

    def schedule_stop():
        cancel_stop()
        def _stop():
            state["stop_timer"] = None
            if state["workflow"] == "idle":
                if POST_ROLL_SEC > 0:
                    time.sleep(POST_ROLL_SEC)
                rec.stop()
        t = threading.Timer(STOP_GRACE_SEC, _stop)
        t.daemon = True
        state["stop_timer"] = t
        t.start()

    def attach(port):
        if port and port != state["port"]:
            log.info("Attaching to machine on %s", port)
            state["port"] = port
            sio.emit("addclient", port)

    @sio.event
    def connect():
        log.info("Connected to gSender")
        state["port"] = None
        sio.emit("list")

    @sio.event
    def disconnect():
        log.warning("Disconnected from gSender")
        state["port"] = None

    @sio.on("serialport:list")
    def on_list(recognized=None, unrecognized=None, network=None, *_):
        for p in (recognized or []) + (unrecognized or []) + (network or []):
            if p and p.get("inuse"):
                attach(p.get("port"))
                return

    @sio.on("serialport:open")
    def on_open(opts=None, *_):
        if isinstance(opts, dict):
            attach(opts.get("port"))

    @sio.on("serialport:close")
    def on_close(*_):
        log.info("Machine disconnected")
        state["port"] = None

    @sio.on("file:load")
    def on_file(gcode=None, size=None, name=None, *_):
        state["job"] = name
        log.info("File loaded: %s", name)

    @sio.on("workflow:state")
    def on_workflow(new_state=None, *_):
        if new_state == state["workflow"]:
            return
        log.info("Workflow: %s -> %s", state["workflow"], new_state)
        state["workflow"] = new_state
        if new_state == "running":
            cancel_stop()
            rec.start(state["job"])
        elif new_state == "idle":
            schedule_stop()
        # paused: keep recording

    @sio.on("job:start")
    def on_job_start(*_):
        cancel_stop()
        rec.start(state["job"])

    def shutdown(*_):
        log.info("Shutting down")
        cancel_stop()
        rec.stop()
        try:
            sio.disconnect()
        except Exception:
            pass
        os._exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    cleanup_old()
    while True:
        if not sio.connected:
            for url in find_gsender_urls():
                try:
                    sio.connect(url, transports=["websocket", "polling"],
                                socketio_path="/socket.io", wait_timeout=5)
                    log.info("Using %s", url)
                    break
                except Exception as e:
                    log.debug("No gSender at %s (%s)", url, e)
            else:
                time.sleep(5)
                continue
        # Machine connected in gSender after we started? Ask again.
        if state["port"] is None and sio.connected:
            sio.emit("list")
        time.sleep(10)


if __name__ == "__main__":
    import sys
    if "--apply-controls" in sys.argv:
        # Used by the camera-stream service at startup.
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
        apply_camera_controls()
        sys.exit(0)
    main()
