from __future__ import annotations

import os
import hashlib
import threading
from datetime import datetime, timedelta
from pathlib import Path
import click
import requests
from requests.auth import HTTPDigestAuth
from flask import (Flask, render_template, jsonify, send_file,
                   send_from_directory, request, abort, redirect, url_for,
                   Response)
from flask_login import LoginManager, login_required, current_user

from logger_config import get_loggers
from time import monotonic
from models import db, User
from auth import auth_bp, admin_required
from admin import admin_bp

app = Flask(__name__)
app_log, http_log, auth_log, ptz_log, privacy_log = get_loggers()

# ── Auth / DB config ──────────────────────────────────────
app.config["SECRET_KEY"]                     = os.environ["SECRET_KEY"]
app.config["SQLALCHEMY_DATABASE_URI"]        = os.environ.get("DB_PATH", "sqlite:///gouda-gaze.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# ── Cookie hardening ──────────────────────────────────────
# HttpOnly — JS cannot read the session cookie (XSS mitigation).
# SameSite=Lax — cookie is not sent on cross-site POST requests (CSRF
#   mitigation). Combined with state-changing endpoints now requiring POST,
#   this blocks cross-site form/fetch CSRF without a token.
# Secure — only transmit over HTTPS. Currently False because the app runs
#   over plain HTTP on Tailscale.
#   HTTPS HOOK (Phase 2/Caddy): flip both Secure flags to True once HTTPS
#   is in place. Can also be driven by an env var:
#   HTTPS_ENABLED=true → set True, default False.
_https = os.environ.get("HTTPS_ENABLED", "false").lower() == "true"

app.config["SESSION_COOKIE_HTTPONLY"]  = True
app.config["SESSION_COOKIE_SAMESITE"]  = "Lax"
app.config["SESSION_COOKIE_SECURE"]    = _https

app.config["REMEMBER_COOKIE_HTTPONLY"] = True
app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"
app.config["REMEMBER_COOKIE_SECURE"]   = _https
app.config["REMEMBER_COOKIE_DURATION"] = timedelta(days=30)

db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = "auth.login"
login_manager.login_message = ""   # suppress default flash

@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    return User.query.get(int(user_id))

app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)

# ── IP helper ────────────────────────────────────────────
def get_client_ip() -> str:
    """
    Return the real client IP.
    PROXY HOOK (Phase 2/Caddy): when a reverse proxy is in front, remote_addr
    will be 127.0.0.1. Uncomment the X-Forwarded-For line and configure Caddy
    to set it. Validate the proxy IP before trusting the header in production.
    """
    # forwarded = request.headers.get("X-Forwarded-For", "")
    # if forwarded:
    #     return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


# ── HTTP access log (every request) ──────────────────────
@app.before_request
def _start_timer():
    request._start_time = monotonic()


@app.after_request
def _log_request(response):
    elapsed_ms = round((monotonic() - getattr(request, "_start_time", monotonic())) * 1000)
    try:
        from flask_login import current_user
        user = current_user.email if current_user.is_authenticated else "[anonymous]"
    except Exception:
        user = "[unknown]"
    # Skip static assets — noisy and low value
    if not request.path.startswith("/static/"):
        http_log.info(
            f"{request.method} {request.path} {response.status_code}"
            f" | {user} | {get_client_ip()} | {elapsed_ms}ms"
        )
    return response


# Inject pending count into all templates so the nav badge works
@app.context_processor
def inject_globals():
    count = 0
    if current_user.is_authenticated and current_user.is_admin:
        count = User.query.filter_by(status="pending").count()
    return {"pending_count": count}

# ── CLI: bootstrap first admin ────────────────────────────
@app.cli.command("create-admin")
@click.option("--email",    prompt="Admin email")
@click.option("--name",     prompt="Admin name")
@click.option("--password", prompt="Password", hide_input=True, confirmation_prompt=True)
def create_admin(email: str, name: str, password: str) -> None:
    """Bootstrap the first admin user (run once after deploy)."""
    db.create_all()
    if User.query.filter_by(email=email.lower()).first():
        click.echo(f"Error: {email} already exists.")
        return
    user = User(email=email.lower(), name=name, role="admin", status="approved")
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    click.echo(f"Admin user {email} created.")

# --- Amcrest camera config from environment ---
CAM_IP      = os.environ["CAM_IP"]
CAM_USER    = os.environ["CAM_USER"]
CAM_PASS    = os.environ["CAM_PASS"]
CAM_CHANNEL = os.environ["CAM_CHANNEL"]
PTZ_SPEED   = int(os.environ["PTZ_SPEED"])

RPC2_URL       = f"http://{CAM_IP}/RPC2"
RPC2_LOGIN_URL = f"http://{CAM_IP}/RPC2_Login"

# --- Snapshot storage ---
# NAS HOOK: Replace SNAPSHOT_DIR with a NAS mount path when network storage is added.
# e.g. SNAPSHOT_DIR = Path(os.environ.get("NAS_SNAPSHOT_PATH", "./snapshots"))
# For SMB/NFS mounts, ensure the mount is present before the app starts.
# For S3/object storage, swap save_snapshot() to use boto3 instead of Path.write_bytes().
SNAPSHOT_DIR = Path("./snapshots")
SNAPSHOT_DIR.mkdir(exist_ok=True)

# --- Privacy state ---
# ACL HOOK: Replace with per-user lookup when per-user privacy is implemented.
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

VALID_RESOLUTIONS  = ["2560x1440", "1920x1080", "1280x720", "640x480"]
VALID_FPS          = [5, 10, 15, 20, 25, 30]
VALID_BITRATE_CTRL = ["CBR", "VBR"]
BITRATE_RANGE      = (512, 8192)


# ── RPC2 auth ─────────────────────────────────────────────

def _MD5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest().upper()


def _rpc2_hash(realm: str, random: str) -> str:
    step1 = _MD5(f"{CAM_USER}:{realm}:{CAM_PASS}")
    return _MD5(f"{CAM_USER}:{random}:{step1}")


def _rpc2_login() -> str | None:
    try:
        r1 = requests.post(RPC2_LOGIN_URL, json={
            "method": "global.login",
            "params": {"userName": CAM_USER, "password": "", "clientType": "Web3.0", "loginType": "Direct"},
            "id": 1
        }, timeout=5)
        if not r1.text.strip():
            app_log.error("RPC2 login step1: empty response")
            return None
        d1      = r1.json()
        p       = d1.get("params", {})
        realm   = p.get("realm", "")
        random  = p.get("random", "")
        session = d1.get("session", "")
        if not all([realm, random, session]):
            app_log.error(f"RPC2 login step1 missing fields: {d1}")
            return None
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


_sync_privacy_from_camera()


# ── Snapshot storage ──────────────────────────────────────

def save_snapshot(jpeg_bytes: bytes, filename: str) -> Path:
    """
    Persist a snapshot to storage and return its path.

    NAS HOOK: This is the single function to modify when adding network storage.
    Current: writes to local SNAPSHOT_DIR.
    Future options:
      - NFS/SMB mount: change SNAPSHOT_DIR to the mount point path
      - S3: replace Path.write_bytes() with boto3 put_object()
      - NAS API: POST jpeg_bytes to NAS endpoint
    """
    dest = SNAPSHOT_DIR / filename
    dest.write_bytes(jpeg_bytes)
    app_log.info(f"Snapshot saved: {filename} ({len(jpeg_bytes)} bytes)")
    return dest


def list_snapshots() -> list[dict]:
    """
    Return metadata for all saved snapshots, newest first.

    NAS HOOK: Replace directory scan with NAS API listing or S3 list_objects()
    when network storage is added. Response shape should stay the same so the
    gallery frontend doesn't need to change.

    Future: add pagination support by accepting offset/limit params.
    """
    snapshots = []
    for f in sorted(SNAPSHOT_DIR.glob("*.jpg"), reverse=True):
        stat = f.stat()
        snapshots.append({
            "filename":  f.name,
            "timestamp": f.stem.replace("_", " ", 1).replace("-", "/", 2),
            "size_kb":   round(stat.st_size / 1024, 1),
            "url":       f"/snapshots/{f.name}",
        })
    return snapshots


# ── Stream settings (CGI) ─────────────────────────────────

def _parse_encode_response(text: str) -> dict:
    result = {}
    prefix = "table.Encode[0].MainFormat[0].Video."
    for line in text.splitlines():
        if line.startswith(prefix):
            key, _, val = line[len(prefix):].partition("=")
            result[key.strip()] = val.strip()
    return result


def get_stream_settings() -> dict | None:
    url = f"http://{CAM_IP}/cgi-bin/configManager.cgi?action=getConfig&name=Encode"
    try:
        resp = requests.get(url, auth=HTTPDigestAuth(CAM_USER, CAM_PASS), timeout=5)
        if resp.status_code != 200:
            app_log.error(f"getConfig Encode failed: {resp.status_code}")
            return None
        parsed = _parse_encode_response(resp.text)
        return {
            "resolution":   parsed.get("resolution", ""),
            "fps":          int(parsed.get("FPS", 15)),
            "bitrate":      int(parsed.get("BitRate", 2048)),
            "bitrate_ctrl": parsed.get("BitRateControl", "VBR"),
        }
    except (requests.RequestException, ValueError) as e:
        app_log.error(f"get_stream_settings error: {e}")
        return None


def set_stream_settings(resolution: str, fps: int, bitrate: int, bitrate_ctrl: str) -> bool:
    try:
        w, h = resolution.split("x")
    except ValueError:
        app_log.error(f"Invalid resolution format: {resolution}")
        return False
    params = (
        f"Encode[0].MainFormat[0].Video.resolution={resolution}"
        f"&Encode[0].MainFormat[0].Video.Width={w}"
        f"&Encode[0].MainFormat[0].Video.Height={h}"
        f"&Encode[0].MainFormat[0].Video.FPS={fps}"
        f"&Encode[0].MainFormat[0].Video.BitRate={bitrate}"
        f"&Encode[0].MainFormat[0].Video.BitRateControl={bitrate_ctrl}"
    )
    url = f"http://{CAM_IP}/cgi-bin/configManager.cgi?action=setConfig&{params}"
    try:
        resp = requests.get(url, auth=HTTPDigestAuth(CAM_USER, CAM_PASS), timeout=5)
        ok = resp.status_code == 200 and "OK" in resp.text
        if ok:
            app_log.info(f"Stream settings updated: {resolution} {fps}fps {bitrate}Kbps {bitrate_ctrl}")
        else:
            app_log.error(f"setConfig Encode failed: {resp.status_code} {resp.text}")
        return ok
    except requests.RequestException as e:
        app_log.error(f"set_stream_settings error: {e}")
        return False


# ── CGI helpers ───────────────────────────────────────────

def is_privacy_on() -> bool:
    # ACL HOOK: Check requesting user's privacy state here when per-user privacy is added.
    return _privacy_enabled


def ptz_command(action: str, code: str) -> bool:
    url = (
        f"http://{CAM_IP}/cgi-bin/ptz.cgi"
        f"?action={action}&channel={CAM_CHANNEL}"
        f"&code={code}&arg1={PTZ_SPEED}&arg2={PTZ_SPEED}&arg3=0"
    )
    ptz_log.debug(f"PTZ URL: {url}")
    try:
        resp = requests.get(url, auth=HTTPDigestAuth(CAM_USER, CAM_PASS), timeout=3)
        ok = resp.status_code == 200
        ptz_log.info(f"PTZ {action} {code} -> {resp.status_code}")
        if not ok:
            ptz_log.error(f"PTZ {action} {code} failed — camera body: {resp.text[:200]!r}")
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

@app.route("/")
@login_required
def index():
    _sync_privacy_from_camera()
    return render_template("index.html", privacy=is_privacy_on())


@app.route("/gallery")
@login_required
def gallery():
    return render_template("gallery.html")


@app.route("/privacy-image")
@login_required
def privacy_image():
    return send_file("./privacy.png", mimetype="image/png")


@app.route("/snapshots/<filename>")
@login_required
def serve_snapshot(filename: str):
    # Reject path traversal attempts and non-JPEG files.
    # send_from_directory is already safe against directory escape, but
    # explicitly restricting to .jpg prevents serving unexpected file types
    # if anything other than snapshots ended up in SNAPSHOT_DIR.
    if "/" in filename or ".." in filename:
        abort(400)
    if not filename.lower().endswith(".jpg"):
        abort(400)
    return send_from_directory(SNAPSHOT_DIR, filename, mimetype="image/jpeg")


# ── Snapshot API ──────────────────────────────────────────

@app.route("/api/snapshot", methods=["POST"])
@login_required
def take_snapshot():
    if is_privacy_on():
        http_log.info("POST /api/snapshot blocked — privacy mode active")
        return jsonify({"status": "error", "message": "Privacy mode is active"}), 403

    url = f"http://{CAM_IP}/cgi-bin/snapshot.cgi?channel=1&type=0"
    try:
        resp = requests.get(url, auth=HTTPDigestAuth(CAM_USER, CAM_PASS), timeout=10)
        if resp.status_code != 200 or not resp.content:
            http_log.info(f"POST /api/snapshot failed — camera returned {resp.status_code}")
            app_log.error(f"Snapshot failed: {resp.status_code}")
            return jsonify({"status": "error", "message": "Camera snapshot failed"}), 502
    except requests.RequestException as e:
        http_log.info(f"POST /api/snapshot error — camera unreachable ({e})")
        app_log.error(f"Snapshot request error: {e}")
        return jsonify({"status": "error", "message": "Camera unreachable"}), 502

    filename = datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".jpg"
    size_kb  = round(len(resp.content) / 1024, 1)
    save_snapshot(resp.content, filename)
    http_log.info(f"POST /api/snapshot success — {filename} ({size_kb} KB) user={current_user.email}")

    return jsonify({
        "status":   "success",
        "filename": filename,
        "url":      f"/snapshots/{filename}",
        "size_kb":  size_kb,
    })


@app.route("/api/snapshots", methods=["GET"])
@login_required
def get_snapshots():
    return jsonify({
        "status":    "success",
        "snapshots": list_snapshots(),
        "total":     len(list(SNAPSHOT_DIR.glob("*.jpg"))),
    })


# ── Privacy API ───────────────────────────────────────────

@app.route("/api/privacy/status")
@login_required
def privacy_status():
    return jsonify({"privacy": is_privacy_on()})


@app.route("/api/privacy/on", methods=["POST"])
@admin_required
def privacy_on():
    global _privacy_enabled
    with _privacy_lock:
        hw_ok = _hw_set_privacy(True)
        if not hw_ok:
            return jsonify({"status": "error", "message": "Camera privacy command failed"}), 502
        _privacy_enabled = True
    privacy_log.info(f"Privacy mode: ON (by {current_user.email})")
    return jsonify({"status": "success", "privacy": True})


@app.route("/api/privacy/off", methods=["POST"])
@admin_required
def privacy_off():
    global _privacy_enabled
    with _privacy_lock:
        hw_ok = _hw_set_privacy(False)
        if not hw_ok:
            return jsonify({"status": "error", "message": "Camera privacy command failed"}), 502
        _privacy_enabled = False
    privacy_log.info(f"Privacy mode: OFF (by {current_user.email})")
    return jsonify({"status": "success", "privacy": False})


# ── Stream settings API ───────────────────────────────────

@app.route("/api/stream/settings", methods=["GET"])
@admin_required
def stream_settings_get():
    settings = get_stream_settings()
    if settings is None:
        return jsonify({"status": "error", "message": "Could not read stream settings"}), 502
    return jsonify({"status": "success", "settings": settings})


@app.route("/api/stream/settings", methods=["POST"])
@admin_required
def stream_settings_set():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No data provided"}), 400

    resolution   = data.get("resolution", "")
    fps          = data.get("fps")
    bitrate      = data.get("bitrate")
    bitrate_ctrl = data.get("bitrate_ctrl", "VBR")

    if resolution not in VALID_RESOLUTIONS:
        return jsonify({"status": "error", "message": f"Invalid resolution: {resolution}"}), 400
    if fps not in VALID_FPS:
        return jsonify({"status": "error", "message": f"Invalid FPS: {fps}"}), 400
    if not isinstance(bitrate, int) or not (BITRATE_RANGE[0] <= bitrate <= BITRATE_RANGE[1]):
        return jsonify({"status": "error", "message": f"Bitrate must be {BITRATE_RANGE[0]}–{BITRATE_RANGE[1]} Kbps"}), 400
    if bitrate_ctrl not in VALID_BITRATE_CTRL:
        return jsonify({"status": "error", "message": f"Invalid bitrate control: {bitrate_ctrl}"}), 400

    ok = set_stream_settings(resolution, fps, bitrate, bitrate_ctrl)
    if not ok:
        return jsonify({"status": "error", "message": "Failed to apply stream settings"}), 502
    return jsonify({"status": "success", "message": "Stream settings saved"})


# ── PTZ API ───────────────────────────────────────────────

@app.route("/api/move/start/<direction>", methods=["POST"])
@login_required
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


@app.route("/api/move/stop/<direction>", methods=["POST"])
@login_required
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


@app.route("/api/home", methods=["POST"])
@login_required
def home_camera():
    if is_privacy_on():
        return jsonify({"status": "error", "message": "Privacy mode is active"}), 403
    ok = ptz_preset(1)
    if not ok:
        return jsonify({"status": "error", "message": "Home preset failed"}), 502
    ptz_log.info(f"Homed to preset 1 (by {current_user.email})")
    return jsonify({"status": "success", "action": "home"})



# ── go2rtc stream proxy ───────────────────────────────────
#
# go2rtc is bound to 127.0.0.1:1984 (localhost only) — port 1984
# is invisible on the network. Flask is the only path to the stream,
# so @login_required is enforced for all signaling.
#
# We use go2rtc's HTTP WebRTC API (POST /api/webrtc) instead of its
# WebSocket signaling (api/ws). The requests library cannot proxy
# WebSocket upgrades, but the HTTP API is a plain POST:
#   client sends SDP offer → go2rtc returns SDP answer → WebRTC starts
#
# The browser's WebRTC peer connection is established directly between
# the client and the Pi after signaling; Flask is not in the media path.

GO2RTC_ORIGIN = "http://127.0.0.1:1984"


@app.route("/stream/webrtc", methods=["POST"])
@login_required
def stream_webrtc_signal():
    """
    WebRTC HTTP signaling endpoint.
    Accepts: SDP offer (text/plain or application/sdp)
    Returns: SDP answer (text/plain)
    Proxies to go2rtc's POST /api/webrtc?src=<stream>
    """
    if is_privacy_on():
        return jsonify({"status": "error", "message": "Privacy mode is active"}), 403

    # Whitelist the stream name — only forward to known go2rtc streams
    src = request.args.get("src", "cam")
    if src not in ("cam",):
        app_log.warning(f"stream_webrtc_signal: unknown src {src!r} from {get_client_ip()}")
        return jsonify({"status": "error", "message": "Unknown stream"}), 400

    # ── Fix 3: SDP size limit (valid SDPs are well under 8 KB; 64 KB is generous) ──
    max_sdp = 65_536
    content_length = request.content_length
    if content_length and content_length > max_sdp:
        app_log.warning(f"stream_webrtc_signal: oversized offer ({content_length}B) from {get_client_ip()}")
        return jsonify({"status": "error", "message": "SDP offer too large"}), 413

    offer_sdp = request.get_data(as_text=True, cache=False)

    if len(offer_sdp) > max_sdp:
        app_log.warning(f"stream_webrtc_signal: oversized offer body from {get_client_ip()}")
        return jsonify({"status": "error", "message": "SDP offer too large"}), 413

    if not offer_sdp.strip().startswith("v="):
        app_log.warning(f"stream_webrtc_signal: invalid SDP from {get_client_ip()}")
        return jsonify({"status": "error", "message": "Invalid SDP offer"}), 400

    try:
        resp = requests.post(
            f"{GO2RTC_ORIGIN}/api/webrtc",
            params={"src": src},
            data=offer_sdp,
            headers={"Content-Type": "application/sdp"},
            timeout=10,
        )
    except requests.RequestException as e:
        app_log.error(f"go2rtc signaling error: {e}")
        return jsonify({"status": "error", "message": "Stream unavailable"}), 502

    # go2rtc returns 201 (WHEP standard) for a successful offer/answer exchange
    if resp.status_code not in (200, 201):
        app_log.error(f"go2rtc returned unexpected {resp.status_code}: {resp.text[:400]!r}")
        return jsonify({"status": "error", "message": f"go2rtc error {resp.status_code}"}), 502

    app_log.debug(f"go2rtc SDP answer ({resp.status_code}):\n{resp.text}")
    return Response(resp.text, content_type="application/sdp")


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app_log.info("Gouda Gaze starting")
    app.run(host="0.0.0.0", port=1122, debug=False)