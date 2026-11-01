#!/usr/bin/env bash
# Installs (or updates) the gSender job recorder and camera stream on the Pi.
# Run from the folder containing gsender_recorder.py:   bash install.sh
#
# Safe to re-run: your settings in /etc/gsender-recorder.env are kept.
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
  echo "Please run this as your normal user (the one that runs gSender), not root:"
  echo "  bash install.sh"
  echo "It will ask for your sudo password when it needs it."
  exit 1
fi

RUN_USER="$USER"
RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
ENV_FILE=/etc/gsender-recorder.env
OLD_UNIT=/etc/systemd/system/gsender-recorder.service

echo "Installing packages..."
sudo apt-get update -qq
sudo apt-get install -y ffmpeg v4l-utils ustreamer \
  python3-socketio python3-websocket python3-requests

echo "Installing script to /opt/gsender-recorder..."
sudo mkdir -p /opt/gsender-recorder
sudo install -m 755 gsender_recorder.py /opt/gsender-recorder/gsender_recorder.py
sudo usermod -aG video,audio "$RUN_USER"

# ---------------------------------------------------------------- settings
# Defaults for a fresh install. Existing values always win.
DEFAULTS="$(cat <<EOF
OUTPUT_DIR=$RUN_HOME/cnc-videos
VIDEO_DEVICE=/dev/video0
VIDEO_SIZE=1920x1080
VIDEO_FPS=30
VIDEO_FORMAT=mjpeg
KEEP_DAYS=14
CAMERA_CONTROLS=exposure_dynamic_framerate=0,focus_automatic_continuous=0,focus_absolute=0
AUDIO_DEVICE=plughw:CARD=C920,DEV=0
STREAM_PORT=8080
STREAM_URL=http://127.0.0.1:8080/stream
EOF
)"

TMP="$(mktemp)"
if sudo test -f "$ENV_FILE"; then
  echo "Keeping your existing settings in $ENV_FILE"
  sudo cat "$ENV_FILE" > "$TMP"
elif sudo test -f "$OLD_UNIT"; then
  echo "Moving your settings from the old service file into $ENV_FILE"
  { echo "# gSender recorder settings (moved from the service file)"
    sudo grep '^Environment=' "$OLD_UNIT" | sed 's/^Environment=//'
  } > "$TMP"
else
  echo "# gSender recorder settings. Edit, then:" > "$TMP"
  echo "#   sudo systemctl restart cnc-camera gsender-recorder" >> "$TMP"
fi
# Add any setting that isn't there yet (new features), without touching others.
while IFS= read -r line; do
  key="${line%%=*}"
  grep -q "^${key}=" "$TMP" || echo "$line" >> "$TMP"
done <<< "$DEFAULTS"
sudo install -m 644 "$TMP" "$ENV_FILE"
rm -f "$TMP"

# ---------------------------------------------------------------- camera stream
echo "Writing camera stream service (ustreamer)..."
sudo tee /etc/systemd/system/cnc-camera.service >/dev/null <<EOF
[Unit]
Description=CNC webcam live stream (ustreamer)
After=network.target

[Service]
User=$RUN_USER
EnvironmentFile=$ENV_FILE
# --encoder=HW passes the camera's own MJPEG frames through: no re-encoding.
ExecStart=/usr/bin/ustreamer --device=\${VIDEO_DEVICE} --format=MJPEG --encoder=HW --resolution=\${VIDEO_SIZE} --desired-fps=\${VIDEO_FPS} --host=0.0.0.0 --port=\${STREAM_PORT} --persistent
# Lock focus/exposure once the camera is open.
ExecStartPost=/bin/sh -c 'sleep 3; /usr/bin/python3 /opt/gsender-recorder/gsender_recorder.py --apply-controls'
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

# ---------------------------------------------------------------- recorder
echo "Writing recorder service..."
sudo tee "$OLD_UNIT" >/dev/null <<EOF
[Unit]
Description=Record webcam while gSender runs a job
After=network.target cnc-camera.service
Wants=cnc-camera.service

[Service]
User=$RUN_USER
EnvironmentFile=$ENV_FILE
ExecStart=/usr/bin/python3 /opt/gsender-recorder/gsender_recorder.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable cnc-camera gsender-recorder
sudo systemctl restart cnc-camera gsender-recorder

PORT="$(grep '^STREAM_PORT=' "$ENV_FILE" | cut -d= -f2)"
echo
echo "Done."
echo "  Live preview:  http://$(hostname).local:${PORT:-8080}/stream"
echo "  Settings:      sudo nano $ENV_FILE"
echo "  Recorder log:  journalctl -u gsender-recorder -f"
echo "  Videos:        $(grep '^OUTPUT_DIR=' "$ENV_FILE" | cut -d= -f2)"
