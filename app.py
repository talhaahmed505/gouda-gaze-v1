import os
import requests
from requests.auth import HTTPDigestAuth
from flask import Flask, render_template, jsonify

app = Flask(__name__)

# --- Amcrest camera config from environment ---
CAM_IP      = os.environ["CAM_IP"]
CAM_USER    = os.environ["CAM_USER"]
CAM_PASS    = os.environ["CAM_PASS"]
CAM_CHANNEL = os.environ["CAM_CHANNEL"]
PTZ_SPEED   = int(os.environ["PTZ_SPEED"])

# Amcrest CGI direction codes
DIRECTION_MAP = {
    "up":    "Up",
    "down":  "Down",
    "left":  "Left",
    "right": "Right",
}

def ptz_command(action: str, code: str) -> bool:
    """
    Send a single PTZ CGI command to the Amcrest camera.
    action: "start" or "stop"
    code:   "Up", "Down", "Left", "Right"
    Returns True on success, False on failure.
    """
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
    """
    Tell the camera to move to a saved preset position.
    preset_id 1 = home (set once in the camera's web UI).
    """
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


@app.route('/')
def index():
    pi_ip = os.environ["PI_IP_TS"]
    return render_template('index.html', pi_ip=pi_ip)


@app.route('/api/move/start/<direction>')
def move_start(direction: str):
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
    """Return camera to preset 1 (home). Digital zoom is reset by the frontend."""
    ok = ptz_preset(1)
    if not ok:
        return jsonify({"status": "error", "message": "Home preset failed"}), 502
    print("PTZ: homed to preset 1")
    return jsonify({"status": "success", "action": "home"})


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=1122, debug=False)