import os
import hashlib
import threading
import requests
from requests.auth import HTTPDigestAuth
from flask import Flask, render_template, jsonify, send_file
from logging_config import get_loggers

app = Flask(__name__)

app_log, http_log, ptz_log, privacy_log = get_loggers()

# --- Amcrest camera config from environment ---
CAM_IP      = os.environ["CAM_IP"]
CAM_USER    = os.environ["CAM_USER"]
CAM_PASS    = os.environ["CAM_PASS"]
CAM_CHANNEL = os.environ["CAM_CHANNEL"]
PTZ_SPEED   = int(os.environ["PTZ_SPEED"])

RPC2_URL       = f"http://{CAM_IP}/RPC2"
RPC2_LOGIN_URL = f"http://{CAM_IP}/RPC2_Login"

# --- Privacy state ---
# _privacy_enabled mirrors the camera's hardware LeLensMask state.
# It is synced from the camera on startup and after every toggle.
# ACL HOOK: Replace with per-user lookup when ACL is implemented.
_privacy_enabled = False
_privacy_lock    = threading.Lock()  # guard concurrent toggle requests

# --- RPC2 session cache ---
# We cache the session token to avoid a full 2-step login on every privacy call.
# The camera issues tokens with keepAliveInterval=60s, so we re-login if stale.
_rpc2_session    = None
_rpc2_session_lock = threading.Lock()


# ── RPC2 auth ────────────────────────────────────────────

def _rpc2_hash(realm: str, random: str) -> str:
    """
    Amcrest RPC2 password hash (captured from browser network traffic):
      step1 = MD5(password).upper()
      step2 = MD5(username:realm:step1).upper()
      final = MD5(step2:random).upper()
    """
    step1 = hashlib.md5(CAM_PASS.encode()).hexdigest().upper()
    step2 = hashlib.md5(f"{CAM_USER}:{realm}:{step1}".encode()).hexdigest().upper()
    return hashlib.md5(f"{step2}:{random}".encode()).hexdigest().upper()


def _rpc2_login() -> str | None:
    """Two-step RPC2 challenge/response login. Returns session token or None."""
    try:
        # Step 1: challenge
        r1 = requests.post(RPC2_LOGIN_URL, json={
            "method": "global.login",
            "params": {"userName": CAM_USER, "password": "", "clientType": "Web3.0", "loginType": "Direct"},
            "id": 1
        }, timeout=5)
        d1 = r1.json()
        p  = d1.get("params", {})
        realm, random, session = p.get("realm",""), p.get("random",""), d1.get("session","")
        if not all([realm, random, session]):
            app_log.error(f"RPC2 login step1 missing fields: {d1}")
            return None

        # Step 2: authenticate
        r2 = requests.post(RPC2_LOGIN_URL, json={
            "method": "global.login",
            "params": {
                "userName": CAM_USER,
                "password": _rpc2_hash(realm, random),
                "clientType": "Web3.0",
                "loginType": "Direct",
                "authorityType": "Default"
            },
            "id": 2,
            "session": session
        }, timeout=5)
        d2 = r2.json()
        if not d2.get("result"):
            app_log.error(f"RPC2 login step2 rejected: {d2}")
            return None

        app_log.debug("RPC2 session acquired")
        return d2.get("session")

    except requests.RequestException as e:
        app_log.error(f"RPC2 login error: {e}")
        return None


def _get_rpc2_session() -> str | None:
    """Return cached session, re-logging in if necessary."""
    global _rpc2_session
    with _rpc2_session_lock:
        if _rpc2_session is None:
            _rpc2_session = _rpc2_login()
        return _rpc2_session


def _invalidate_rpc2_session():
    global _rpc2_session
    with _rpc2_session_lock:
        _rpc2_session = None


def _rpc2_call(method: str, params: dict) -> dict | None:
    """
    Make an authenticated RPC2 call, retrying once if the session has expired.
    Returns the parsed response dict or None on failure.
    """
    for attempt in range(2):
        session = _get_rpc2_session()
        if not session:
            return None
        try:
            resp = requests.post(RPC2_URL, json={
                "method": method,
                "params": params,
                "id": 1,
                "session": session
            }, timeout=5)
            data = resp.json()
            # Session expired — invalidate and retry once
            if not data.get("result") and data.get("error", {}).get("code") in (268632079, 287637505):
                app_log.debug("RPC2 session expired, re-authenticating")
                _invalidate_rpc2_session()
                continue
            return data
        except requests.RequestException as e:
            app_log.error(f"RPC2 call {method} error: {e}")
            return None
    return None


# ── Hardware privacy (LeLensMask) ─────────────────────────

def _hw_get_privacy() -> bool | None:
    """
    Read the camera's current LeLensMask state.
    Returns True (privacy on), False (privacy off), or None on error.
    """
    data = _rpc2_call("configManager.getConfig", {"name": "LeLensMask"})
    if not data or not data.get("result"):
        app_log.error(f"LeLensMask getConfig failed: {data}")
        return None
    try:
        enabled = data["params"]["table"][0]["Enable"]
        return bool(enabled)
    except (KeyError, IndexError, TypeError) as e:
        app_log.error(f"LeLensMask getConfig parse error: {e} — response: {data}")
        return None


def _hw_set_privacy(enable: bool) -> bool:
    """
    Set the camera's LeLensMask state.
    Reads current config first to preserve LastPosition and TimeSection.
    Returns True on success.
    """
    # Read current table to preserve all other fields
    get_data = _rpc2_call("configManager.getConfig", {"name": "LeLensMask"})
    if not get_data or not get_data.get("result"):
        app_log.error(f"LeLensMask getConfig failed before set: {get_data}")
        return False

    try:
        table = get_data["params"]["table"]
    except (KeyError, TypeError):
        app_log.error(f"LeLensMask getConfig unexpected structure: {get_data}")
        return False

    table[0]["Enable"] = enable

    set_data = _rpc2_call("configManager.setConfig", {
        "name": "LeLensMask",
        "table": table,
        "options": []
    })

    if not set_data or not set_data.get("result"):
        app_log.error(f"LeLensMask setConfig failed: {set_data}")
        return False

    return True


def _sync_privacy_from_camera():
    """
    Read hardware state on startup and sync _privacy_enabled to match.
    Prevents desync if someone toggled privacy from the camera's own web UI.
    """
    global _privacy_enabled
    hw_state = _hw_get_privacy()
    if hw_state is None:
        app_log.warning("Could not read camera privacy state on startup — defaulting to off")
        hw_state = False
    with _privacy_lock:
        _privacy_enabled = hw_state
    privacy_log.info(f"Startup sync: privacy={'ON' if hw_state else 'OFF'}")


# Run sync at startup
_sync_privacy_from_camera()


# ── CGI helpers ───────────────────────────────────────────

def is_privacy_on() -> bool:
    # ACL HOOK: When ACL is added, check the requesting user's privacy state here.
    return _privacy_enabled


def ptz_command(action: str, code: str) -> bool:
    url = (
        f"http://{CAM_IP}/cgi-bin/ptz.cgi"
        f"?action={action}&channel={CAM_CHANNEL}"
        f"&code={code}&arg1={PTZ_SPEED}&arg2={PTZ_SPEED}&arg3=0"
    )
    try:
        resp = requests.get(url, auth=HTTPDigestAuth(CAM_USER, CAM_PASS), timeout=3)
        ok = resp.status_code == 200
        ptz_log.info(f"PTZ {action} {code} -> {resp.status_code}")
        return ok
    except requests.RequestException as e:
        ptz_log.error(f"PTZ error ({action} {code}): {e}")
        return False


def ptz_preset(preset_id: int = 1) -> bool:
    url = (
        f"http://{CAM_IP}/cgi-bin/ptz.cgi"
        f"?action=start&channel={CAM_CHANNEL}"
        f"&code=GotoPreset&arg1=0&arg2={preset_id}&arg3=0"
    )
    try:
        resp = requests.get(url, auth=HTTPDigestAuth(CAM_USER, CAM_PASS), timeout=3)
        ok = resp.status_code == 200
        ptz_log.info(f"PTZ GotoPreset {preset_id} -> {resp.status_code}")
        return ok
    except requests.RequestException as e:
        ptz_log.error(f"PTZ preset error: {e}")
        return False


# ── Routes ────────────────────────────────────────────────

@app.route('/')
def index():
    pi_ip = os.environ["PI_IP_TS"]
    # Re-sync from hardware on every page load so the button always reflects
    # the camera's true state, even if toggled from another client.
    _sync_privacy_from_camera()
    http_log.info(f"GET / privacy={'ON' if is_privacy_on() else 'OFF'}")
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

    with _privacy_lock:
        # 1. Tell the camera hardware to engage privacy mode
        hw_ok = _hw_set_privacy(True)
        if not hw_ok:
            privacy_log.error("Hardware privacy ON failed")
            return jsonify({"status": "error", "message": "Camera privacy command failed"}), 502

        # 2. Mirror state in software — stream will be blocked on next page render
        _privacy_enabled = True

    privacy_log.info("Privacy mode: ON (hardware + software)")
    return jsonify({"status": "success", "privacy": True})


@app.route('/api/privacy/off', methods=['POST'])
def privacy_off():
    # ACL HOOK: Check if requesting user has permission to disable privacy mode.
    global _privacy_enabled

    with _privacy_lock:
        # 1. Tell the camera hardware to disengage privacy mode
        hw_ok = _hw_set_privacy(False)
        if not hw_ok:
            privacy_log.error("Hardware privacy OFF failed")
            return jsonify({"status": "error", "message": "Camera privacy command failed"}), 502

        # 2. Mirror state in software — stream will resume on next page render
        _privacy_enabled = False

    privacy_log.info("Privacy mode: OFF (hardware + software)")
    return jsonify({"status": "success", "privacy": False})


@app.route('/api/move/start/<direction>')
def move_start(direction: str):
    if is_privacy_on():
        return jsonify({"status": "error", "message": "Privacy mode is active"}), 403
    direction = direction.lower()
    if direction not in {
        "up": "Up", "down": "Down", "left": "Left", "right": "Right"
    }:
        return jsonify({"status": "error", "message": f"Unknown direction: {direction}"}), 400
    code_map = {"up": "Up", "down": "Down", "left": "Left", "right": "Right"}
    ok = ptz_command("start", code_map[direction])
    if not ok:
        return jsonify({"status": "error", "message": "Camera command failed"}), 502
    return jsonify({"status": "success", "action": "start", "direction": direction})


@app.route('/api/move/stop/<direction>')
def move_stop(direction: str):
    if is_privacy_on():
        return jsonify({"status": "error", "message": "Privacy mode is active"}), 403
    direction = direction.lower()
    code_map = {"up": "Up", "down": "Down", "left": "Left", "right": "Right"}
    if direction not in code_map:
        return jsonify({"status": "error", "message": f"Unknown direction: {direction}"}), 400
    ok = ptz_command("stop", code_map[direction])
    if not ok:
        return jsonify({"status": "error", "message": "Camera command failed"}), 502
    return jsonify({"status": "success", "action": "stop", "direction": direction})


@app.route('/api/home')
def home_camera():
    if is_privacy_on():
        return jsonify({"status": "error", "message": "Privacy mode is active"}), 403
    ok = ptz_preset(1)
    if not ok:
        return jsonify({"status": "error", "message": "Home preset failed"}), 502
    ptz_log.info("Homed to preset 1")
    return jsonify({"status": "success", "action": "home"})


if __name__ == "__main__":
    app_log.info("Gouda Gaze starting")
    app.run(host='0.0.0.0', port=1122, debug=False)