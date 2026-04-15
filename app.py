from __future__ import annotations
import os
import hashlib
import threading
import requests
from requests.auth import HTTPDigestAuth
from flask import Flask, render_template, jsonify, send_file
from logger_config import get_loggers

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
# ACL HOOK: Replace with per-user lookup when ACL is implemented.
_privacy_enabled = False
_privacy_lock    = threading.Lock()

# --- RPC2 session cache ---
_rpc2_session      = None
_rpc2_session_lock = threading.Lock()

DIRECTION_MAP = {
    "up":    "Up",
    "down":  "Down",
    "left":  "Left",
    "right": "Right",
}


# ── RPC2 auth ─────────────────────────────────────────────
#
# Formula confirmed by intercepting faultylabs.MD5 calls in browser console:
#   step1 = MD5(username + ":" + realm + ":" + password)
#   step2 = MD5(username + ":" + random + ":" + step1)
# Both steps use uppercase hex output.

def _MD5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest().upper()


def _rpc2_hash(realm: str, random: str) -> str:
    step1 = _MD5(f"{CAM_USER}:{realm}:{CAM_PASS}")
    return _MD5(f"{CAM_USER}:{random}:{step1}")


def _rpc2_login() -> str | None:
    """Two-step RPC2 challenge/response. Returns session token or None."""
    try:
        # Step 1: get challenge
        r1 = requests.post(RPC2_LOGIN_URL, json={
            "method": "global.login",
            "params": {"userName": CAM_USER, "password": "", "clientType": "Web3.0", "loginType": "Direct"},
            "id": 1
        }, timeout=5)
        if not r1.text.strip():
            app_log.error("RPC2 login step1: empty response")
            return None
        d1 = r1.json()
        p       = d1.get("params", {})
        realm   = p.get("realm", "")
        random  = p.get("random", "")
        session = d1.get("session", "")
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
        if not r2.text.strip():
            app_log.error("RPC2 login step2: empty response")
            return None
        d2 = r2.json()
        if not d2.get("result"):
            app_log.error(f"RPC2 login step2 rejected: {d2}")
            return None

        app_log.info("RPC2 session acquired")
        return d2.get("session")

    except requests.RequestException as e:
        app_log.error(f"RPC2 login error: {e}")
        return None


def _get_rpc2_session() -> str | None:
    """Return cached session, re-logging in if stale."""
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
    """Authenticated RPC2 call with one automatic session retry."""
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
            if not resp.text.strip():
                return None
            data = resp.json()
            # Session expired — invalidate and retry
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
    data = _rpc2_call("configManager.getConfig", {"name": "LeLensMask"})
    if not data or not data.get("result"):
        privacy_log.error(f"LeLensMask getConfig failed: {data}")
        return None
    try:
        return bool(data["params"]["table"][0]["Enable"])
    except (KeyError, IndexError, TypeError) as e:
        privacy_log.error(f"LeLensMask getConfig parse error: {e}")
        return None


def _hw_set_privacy(enable: bool) -> bool:
    get_data = _rpc2_call("configManager.getConfig", {"name": "LeLensMask"})
    if not get_data or not get_data.get("result"):
        privacy_log.error(f"LeLensMask getConfig failed before set: {get_data}")
        return False
    try:
        table = get_data["params"]["table"]
    except (KeyError, TypeError):
        privacy_log.error(f"LeLensMask unexpected structure: {get_data}")
        return False

    table[0]["Enable"] = enable
    set_data = _rpc2_call("configManager.setConfig", {
        "name": "LeLensMask",
        "table": table,
        "options": []
    })
    if not set_data or not set_data.get("result"):
        privacy_log.error(f"LeLensMask setConfig failed: {set_data}")
        return False
    return True


def _sync_privacy_from_camera():
    global _privacy_enabled
    hw_state = _hw_get_privacy()
    if hw_state is None:
        privacy_log.warning("Could not read camera privacy state — keeping current value")
        return
    _privacy_enabled = hw_state
    privacy_log.info(f"Privacy sync: {'ON' if hw_state else 'OFF'}")


# Sync on startup
_sync_privacy_from_camera()


# ── CGI helpers ───────────────────────────────────────────

def is_privacy_on() -> bool:
    # ACL HOOK: Check requesting user's privacy state here when ACL is added.
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
    _sync_privacy_from_camera()
    http_log.info(f"GET / privacy={'ON' if is_privacy_on() else 'OFF'}")
    return render_template('index.html', pi_ip=pi_ip, privacy=is_privacy_on())


@app.route('/privacy-image')
def privacy_image():
    return send_file('./privacy.png', mimetype='image/png')


@app.route('/api/privacy/status')
def privacy_status():
    return jsonify({"privacy": is_privacy_on()})


@app.route('/api/privacy/on', methods=['POST'])
def privacy_on():
    # ACL HOOK: Check if requesting user has permission to enable privacy mode.
    global _privacy_enabled
    with _privacy_lock:
        hw_ok = _hw_set_privacy(True)
        if not hw_ok:
            return jsonify({"status": "error", "message": "Camera privacy command failed"}), 502
        _privacy_enabled = True
    privacy_log.info("Privacy mode: ON")
    return jsonify({"status": "success", "privacy": True})


@app.route('/api/privacy/off', methods=['POST'])
def privacy_off():
    # ACL HOOK: Check if requesting user has permission to disable privacy mode.
    global _privacy_enabled
    with _privacy_lock:
        hw_ok = _hw_set_privacy(False)
        if not hw_ok:
            return jsonify({"status": "error", "message": "Camera privacy command failed"}), 502
        _privacy_enabled = False
    privacy_log.info("Privacy mode: OFF")
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