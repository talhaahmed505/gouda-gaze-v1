import os
import requests
from requests.auth import HTTPDigestAuth
from flask import Flask, render_template, jsonify, send_file

app = Flask(__name__)

# --- Amcrest camera config from environment ---
CAM_IP      = os.environ["CAM_IP"]
CAM_USER    = os.environ["CAM_USER"]
CAM_PASS    = os.environ["CAM_PASS"]
CAM_CHANNEL = os.environ["CAM_CHANNEL"]
PTZ_SPEED   = int(os.environ["PTZ_SPEED"])

# --- Privacy state (in-memory) ---
# ACL HOOK: Replace this with a per-user or per-role lookup when ACL is implemented.
# e.g. privacy_state = {user_id: bool} keyed off session/token
_privacy_enabled = False

# Amcrest CGI direction codes
DIRECTION_MAP = {
    "up":    "Up",
    "down":  "Down",
    "left":  "Left",
    "right": "Right",
}


def is_privacy_on() -> bool:
    # ACL HOOK: When ACL is added, check the requesting user's privacy state here.
    # e.g. return privacy_state.get(get_current_user(), False)
    return _privacy_enabled


def ptz_command(action: str, code: str) -> bool:
    url = (
        f"http://{CAM_IP}/cgi-bin/ptz.cgi"
        f"?action={action}&channel={CAM_CHANNEL}"
        f"&code={code}&arg1={PTZ_SPEED}&arg2={PTZ_SPEED}&arg3=0"
    )
    try:
        resp = requests.get(url, auth=HTTPDigestAuth(CAM_USER, CAM_PASS), timeout=3)
        return resp.status_code == 200
    except requests.RequestException as e:
        print(f"PTZ error ({action} {code}): {e}")
        return False


def ptz_preset(preset_id: int = 1) -> bool:
    url = (
        f"http://{CAM_IP}/cgi-bin/ptz.cgi"
        f"?action=start&channel={CAM_CHANNEL}"
        f"&code=GotoPreset&arg1=0&arg2={preset_id}&arg3=0"
    )
    try:
        resp = requests.get(url, auth=HTTPDigestAuth(CAM_USER, CAM_PASS), timeout=3)
        return resp.status_code == 200
    except requests.RequestException as e:
        print(f"PTZ preset error: {e}")
        return False


# ── Routes ────────────────────────────────────────────────

@app.route('/')
def index():
    pi_ip = os.environ["PI_IP_TS"]
    return render_template('index.html', pi_ip=pi_ip, privacy=is_privacy_on())


@app.route('/privacy-image')
def privacy_image():
    # Serves the static privacy PNG — the iframe loads this instead of the
    # camera stream when privacy mode is active. Stream never leaves the Pi.
    return send_file('./privacy.png', mimetype='image/png')


@app.route('/api/privacy/status')
def privacy_status():
    return jsonify({"privacy": is_privacy_on()})


@app.route('/api/privacy/on', methods=['POST'])
def privacy_on():
    # ACL HOOK: Check if requesting user has permission to enable privacy mode.
    global _privacy_enabled
    _privacy_enabled = True
    print("Privacy mode: ON")
    return jsonify({"status": "success", "privacy": True})


@app.route('/api/privacy/off', methods=['POST'])
def privacy_off():
    # ACL HOOK: Check if requesting user has permission to disable privacy mode.
    global _privacy_enabled
    _privacy_enabled = False
    print("Privacy mode: OFF")
    return jsonify({"status": "success", "privacy": False})


@app.route('/api/move/start/<direction>')
def move_start(direction: str):
    if is_privacy_on():
        return jsonify({"status": "error", "message": "Privacy mode is active"}), 403
    direction = direction.lower()
    if direction not in DIRECTION_MAP:
        return jsonify({"status": "error", "message": f"Unknown direction: {direction}"}), 400
    ok = ptz_command("start", DIRECTION_MAP[direction])
    if not ok:
        return jsonify({"status": "error", "message": "Camera command failed"}), 502
    print(f"PTZ: start {direction}")
    return jsonify({"status": "success", "action": "start", "direction": direction})


@app.route('/api/move/stop/<direction>')
def move_stop(direction: str):
    if is_privacy_on():
        return jsonify({"status": "error", "message": "Privacy mode is active"}), 403
    direction = direction.lower()
    if direction not in DIRECTION_MAP:
        return jsonify({"status": "error", "message": f"Unknown direction: {direction}"}), 400
    ok = ptz_command("stop", DIRECTION_MAP[direction])
    if not ok:
        return jsonify({"status": "error", "message": "Camera command failed"}), 502
    print(f"PTZ: stop {direction}")
    return jsonify({"status": "success", "action": "stop", "direction": direction})


@app.route('/api/home')
def home_camera():
    if is_privacy_on():
        return jsonify({"status": "error", "message": "Privacy mode is active"}), 403
    ok = ptz_preset(1)
    if not ok:
        return jsonify({"status": "error", "message": "Home preset failed"}), 502
    print("PTZ: homed to preset 1")
    return jsonify({"status": "success", "action": "home"})


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=1122, debug=False)