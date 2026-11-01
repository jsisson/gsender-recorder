# gSender Job Recorder

Records a USB webcam on a Raspberry Pi while gSender (1.6.x) runs a CNC job, and gives you a live preview you can watch at any time, during a job or not, from any browser on your network.

You get one video file per job, named after the G-code file:

```
~/cnc-videos/2026-10-20_18-56-39_tray-pocket.mkv
```

## How it works

Two services run on the Pi:

```
C920 webcam ──► cnc-camera (ustreamer)  ──► live preview in any browser
                    │
                    └──► gsender-recorder ──► one .mkv per job (+ mic audio)
```

**`cnc-camera`** owns the webcam and serves its picture as an MJPEG stream. It passes the camera's own frames straight through without re-encoding, so 1080p costs almost no CPU. Because it's the only program that opens the camera, the preview and the recording no longer block each other.

**`gsender-recorder`** listens to gSender's job state over gSender's local socket.io connection:

| gSender state | Recorder action |
|---|---|
| `running` | Start recording |
| `paused` | Keep recording |
| `idle` | Stop recording a few seconds later |

It saves the stream with `ffmpeg`. The video is copied untouched and the mic audio is encoded as AAC.

Nothing in gSender needs to change. No plugin, macro, or setting is required.

## Requirements

- Raspberry Pi OS (Bookworm or newer) running gSender 1.6.x
- A USB webcam that outputs MJPEG (the Logitech C920 does)
- Internet access during install, to pull packages with `apt`

## Install

Do all of this on the Pi, **as your normal user** (the one that runs gSender), not as root.

1. Install git if it isn't already there, then download this repository:

   ```bash
   sudo apt-get install -y git
   cd ~
   git clone https://github.com/jsisson/gsender-recorder.git
   cd gsender-recorder
   ```

2. Run the installer:

   ```bash
   bash install.sh
   ```

   Don't run it with `sudo`. The script calls `sudo` itself where it needs to and asks for your password. It uses your username to decide who the services run as and where videos are saved.

3. Double-click the **CNC Camera** icon on the desktop to check the live view, then start a job in gSender and check that a video appears in `~/cnc-videos`.

No reboot is needed. The installer adds you to the `video` and `audio` groups (camera and mic access) and then restarts the services, which pick up the new groups as they start. Your own terminal doesn't: if a command you type yourself, such as `v4l2-ctl` or `arecord`, says "Permission denied", log out and back in once.

The downloaded folder only holds the installer. After installing, the services run from `/opt/gsender-recorder/`, so you can keep the folder for updates or delete it.

## Update

```bash
cd ~/gsender-recorder
git pull
bash install.sh
```

If you deleted the folder, clone it again as in step 1.

The installer:

- installs `ffmpeg`, `v4l-utils`, `ustreamer`, and the Python socket.io client
- copies the script and the camera viewer page to `/opt/gsender-recorder/`
- creates the settings file `/etc/gsender-recorder.env`
- creates and starts the `cnc-camera` and `gsender-recorder` services, which start on boot
- adds a **CNC Camera** launcher to the Pi menu (Sound & Video) and the desktop

**Re-running the installer is safe.** Your existing settings, including your focus value, are kept. If you're upgrading from the earlier version, where settings lived inside the service file, they're moved into `/etc/gsender-recorder.env` automatically. Only settings that didn't exist before are added.

## Live preview

### On the Pi: the CNC Camera icon

Double-click **CNC Camera** on the desktop, or find it in the menu under Sound & Video. It opens the camera in its own small window, with no tabs or address bar. Resize and move it like any window; the picture always scales to fit. Double-click the picture to go full screen, and again to come back.

The first time you double-click the desktop icon, the Pi may ask whether to execute the file. Choose **Execute**. To stop it asking, open the File Manager, go to Edit → Preferences, and tick "Don't ask options on launch executable file". You can also right-click **CNC Camera** in the menu and choose "Add to Desktop" or add it to the taskbar.

If Chromium asks to **unlock the keyring** when it opens, the camera won't load until you answer. Enter your login password once.

### From any device on your network

Open this in any browser, on a phone, tablet or laptop:

```
http://cnc.local:8080/
```

Replace `cnc` with your Pi's hostname, or use its IP address. The installer prints the exact address when it finishes.

The page fits the camera to the window in every browser, reconnects by itself if the camera service restarts, and goes full screen on double-click. It works whether or not a job is running, and any number of people can watch at once.

The bare stream is also available at `http://cnc.local:8080/stream`, for other apps that accept an MJPEG URL. When opened directly in a browser, some browsers, including Chromium on the Pi, show it at full size with scrollbars instead of fitting it to the window. Use the page above for viewing.

The camera is open to anyone on your local network, with no password. That's normal for a shop camera on a home network. Don't forward port 8080 on your router.

## Check your camera's modes

Confirm the camera supports the resolution and frame rate in the settings:

```bash
v4l2-ctl -d /dev/video0 --list-formats-ext
```

Look for a `1920x1080` line under `MJPG` and note the fps values listed for it.

## Lock the focus

Autofocus webcams tend to refocus on the gantry as it passes, so the picture pulses during a job. Find a fixed focus value once, while watching the live preview in a browser:

```bash
v4l2-ctl -d /dev/video0 -c focus_automatic_continuous=0
v4l2-ctl -d /dev/video0 -c focus_absolute=40    # try 0–250 in steps of 5
```

Higher numbers focus closer. When the workpiece looks sharp, put that number in `CAMERA_CONTROLS` in the settings file. The camera forgets these values on unplug or reboot, so they're applied when the camera service starts and again before every job.

## Audio

The C920's built-in mic is recorded alongside the video. Find your mic with:

```bash
arecord -l
```

`card 2: C920 [...]` becomes `plughw:CARD=C920,DEV=0`. Use the name rather than the number (`plughw:2,0`), because card numbers can change between reboots. Set `AUDIO_DEVICE=` (empty) to record video only. The live preview has no sound.

Expect the spindle and dust collector to dominate. It's good for hearing chatter or a dull bit, not for narration.

## Settings

All settings are in one file, shared by both services:

```bash
sudo nano /etc/gsender-recorder.env
```

Then apply them:

```bash
sudo systemctl restart cnc-camera gsender-recorder
```

| Setting | Default | Meaning |
|---|---|---|
| `VIDEO_DEVICE` | `/dev/video0` | Webcam device |
| `VIDEO_SIZE` | `1920x1080` | Resolution (must be a mode the camera supports) |
| `VIDEO_FPS` | `30` | Frame rate |
| `STREAM_PORT` | `8080` | Port for the live preview |
| `STREAM_URL` | `http://127.0.0.1:8080/stream` | Where the recorder reads video from. Change the port if you change `STREAM_PORT`. Remove the line to have the recorder open the camera directly; the preview then can't run during jobs |
| `OUTPUT_DIR` | `~/cnc-videos` | Where recordings are saved |
| `CAMERA_CONTROLS` | `exposure_dynamic_framerate=0,focus_automatic_continuous=0,focus_absolute=0` | Webcam controls, comma-separated, applied in order. List yours with `v4l2-ctl -d /dev/video0 --list-ctrls` |
| `AUDIO_DEVICE` | `plughw:CARD=C920,DEV=0` | Mic to record; empty for video only |
| `AUDIO_BITRATE` | `128k` | AAC audio bitrate |
| `KEEP_DAYS` | `14` | Delete recordings older than this; `0` keeps everything |
| `STOP_GRACE_SEC` | `5` | How long gSender must stay idle before stopping |
| `POST_ROLL_SEC` | `3` | Extra seconds recorded after the job ends |
| `MAX_MINUTES` | `600` | Safety cap per recording; `0` means no cap |
| `GSENDER_URL` | auto-detect | Set only if gSender's Remote Mode is on with a fixed port, e.g. `http://127.0.0.1:8000` |

**Disk space:** 1080p MJPEG is roughly 15–25 GB per hour of cutting. Lower `KEEP_DAYS`, or point `OUTPUT_DIR` at a USB SSD, if the SD card fills up.

## Using it

Load a file in gSender and start the job. That's it.

Watch what the recorder is doing:

```bash
journalctl -u gsender-recorder -f
```

A normal job looks like this in the log:

```
Connected to gSender
Attaching to machine on /dev/ttyACM0
File loaded: tray-pocket.nc
Workflow: idle -> running
Recording -> /home/pi/cnc-videos/2026-10-20_18-56-39_tray-pocket.mkv
Workflow: running -> idle
Stopping recording (2412s)
Saved /home/pi/cnc-videos/2026-10-20_18-56-39_tray-pocket.mkv
```

Files are `.mkv`, so a recording stays playable even if the Pi loses power mid-job. VLC plays them directly. To convert one to `.mp4` without re-encoding:

```bash
ffmpeg -i input.mkv -c copy output.mp4
```

## Troubleshooting

**Preview page doesn't load.** On the Pi, check for a hidden "Unlock keyring" window first. Check the camera service with `journalctl -u cnc-camera -f`. "Device or resource busy" means another program has the camera open. Close ffplay, VLC or guvcview; ustreamer must be the only program using it. An error about the resolution or format means `VIDEO_SIZE`/`VIDEO_FPS` don't match a mode the camera offers in MJPEG.

**Focus or exposure settings don't stick.** Look for "Could not set ..." in either service's log. The control name is probably misspelled; compare with `v4l2-ctl --list-ctrls`.

**Never says "Connected to gSender".** gSender must be open. Desktop gSender uses a random port each launch, and the recorder finds it by looking for a listening process with "sender" in its name. If that fails, turn on Remote Mode in gSender with a fixed port and set `GSENDER_URL`.

**Connects but never "Attaching to machine".** Connect to the CNC in gSender first. The recorder re-checks every 10 seconds.

**Recording starts, but the file is empty or ffmpeg errors.** Make sure the preview works. The recorder reads the same stream, so if the preview is broken, recording is too.

**No sound in the recording.** Check the log for an ALSA error. Make sure `AUDIO_DEVICE` matches `arecord -l`, and test with `arecord -D plughw:CARD=C920,DEV=0 -d 5 -f cd test.wav`.

**"Permission denied" on `/dev/video0` or the mic.** In a service log, restart the services so they pick up the `video` and `audio` groups: `sudo systemctl restart cnc-camera gsender-recorder`. For a command you typed yourself, log out and back in once so your terminal session gets the groups too.

## Uninstall

```bash
sudo systemctl disable --now gsender-recorder cnc-camera
sudo rm /etc/systemd/system/gsender-recorder.service /etc/systemd/system/cnc-camera.service
sudo rm -r /opt/gsender-recorder
sudo rm /etc/gsender-recorder.env
rm -f ~/.local/share/applications/cnc-camera.desktop ~/Desktop/cnc-camera.desktop
sudo systemctl daemon-reload
rm -rf ~/gsender-recorder
```

Your recordings in `~/cnc-videos` are left in place.

## Notes

- Built and tested against gSender 1.6.3's socket events. gSender 1.7+ adds a plugin system and may change these events, so re-check after upgrading.
- The recorder only listens. It never sends commands to the machine.
- This is an independent project, not affiliated with or endorsed by Sienci Labs. It contains no gSender code; it only listens to gSender's local socket events.

## License

MIT. See [LICENSE](LICENSE).
