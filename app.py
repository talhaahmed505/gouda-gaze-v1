import os
import time
import requests
from requests.auth import HTTPDigestAuth
from flask import Flask, render_template, jsonify, send_file, request, g
from logger_config import get_loggers

# Initialize loggers
app_logger, http_logger, ptz_logger, privacy_logger = get_loggers()

app = Flask(__name__)

# --- Config ---
CAM_IP      = os.environ["CAM_IP"]
CAM_USER    = os.environ["CAM_USER"]
CAM_PASS    = os.environ["CAM_PASS"]
CAM_CHANNEL = os.environ["CAM_CHANNEL"]
PTZ_SPEED   = int(os.environ["PTZ_SPEED"])

_privacy_enabled = False

DIRECTION_MAP = {
    "up":    "Up",
    "down":  "Down",
    "left":  "Left",
    "right": "Right",
}

# Startup logging
app_logger.info("=" * 80)
app_logger.info("GOUDA GAZE APPLICATION STARTUP")
app_logger.info("=" * 80)
app_logger.info(f"Camera IP: {CAM_IP}")
app_logger.info(f"Camera Channel: {CAM_CHANNEL}")
app_logger.info(f"PTZ Speed: {PTZ_SPEED}")
app_logger.info("=" * 80)


def is_privacy_on() -> bool:
    return _privacy_enabled


def ptz_command(action: str, code: str) -> bool:
    """Execute PTZ command and log result."""
    start_time = time.time()
    
    url = (
        f"http://{CAM_IP}/cgi-bin/ptz.cgi"
        f"?action={action}&channel={CAM_CHANNEL}"
        f"&code={code}&arg1={PTZ_SPEED}&arg2={PTZ_SPEED}&arg3=0"
    )
    
    try:
        resp = requests.get(
            url,
            auth=HTTPDigestAuth(CAM_USER, CAM_PASS),
            timeout=3
        )
        duration_ms = (time.time() - start_time) * 1000
        
        if resp.status_code == 200:
            ptz_logger.info(
                f"SUCCESS | action={action} | code={code} | "
                f"duration_ms={duration_ms:.0f} | status={resp.status_code}"
            )
            return True
        else:
            ptz_logger.warning(
                f"FAILED | action={action} | code={code} | "
                f"status={resp.status_code} | duration_ms={duration_ms:.0f}"
            )
            return False
            
    except requests.Timeout:
        duration_ms = (time.time() - start_time) * 1000
        ptz_logger.error(
            f"TIMEOUT | action={action} | code={code} | "
            f"duration_ms={duration_ms:.0f}"
        )
        return False
        
    except requests.ConnectionError as e:
        duration_ms = (time.time() - start_time) * 1000
        ptz_logger.error(
            f"CONNECTION_ERROR | action={action} | code={code} | "
            f"error={str(e)[:100]} | duration_ms={duration_ms:.0f}"
        )
        return False
        
    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        ptz_logger.error(
            f"EXCEPTION | action={action} | code={code} | "
            f"error={type(e).__name__}: {str(e)[:100]} | duration_ms={duration_ms:.0f}"
        )
        return False


def ptz_preset(preset_id: int = 1) -> bool:
    """Move to preset position and log result."""
    start_time = time.time()
    
    url = (
        f"http://{CAM_IP}/cgi-bin/ptz.cgi"
        f"?action=start&channel={CAM_CHANNEL}"
        f"&code=GotoPreset&arg1=0&arg2={preset_id}&arg3=0"
    )
    
    try:
        resp = requests.get(
            url,
            auth=HTTPDigestAuth(CAM_USER, CAM_PASS),
            timeout=3
        )
        duration_ms = (time.time() - start_time) * 1000
        
        if resp.status_code == 200:
            ptz_logger.info(
                f"PRESET_SUCCESS | preset_id={preset_id} | "
                f"duration_ms={duration_ms:.0f} | status={resp.status_code}"
            )
            return True
        else:
            ptz_logger.warning(
                f"PRESET_FAILED | preset_id={preset_id} | "
                f"status={resp.status_code} | duration_ms={duration_ms:.0f}"
            )
            return False
            
    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        ptz_logger.error(
            f"PRESET_ERROR | preset_id={preset_id} | "
            f"error={type(e).__name__}: {str(e)[:100]} | duration_ms={duration_ms:.0f}"
        )
        return False


# ========== HTTP REQUEST/RESPONSE LOGGING ==========

@app.before_request
def before_request():
    """Log incoming HTTP request."""
    g.start_time = time.time()
    
    # Extract relevant request info
    method = request.method
    path = request.path
    remote_addr = request.remote_addr
    user_agent = request.headers.get('User-Agent', 'Unknown')[:100]
    
    # Log request
    http_logger.info(
        f">>> REQUEST | method={method} | path={path} | "
        f"remote_addr={remote_addr} | user_agent={user_agent}"
    )


@app.after_request
def after_request(response):
    """Log outgoing HTTP response."""
    duration_ms = (time.time() - g.start_time) * 1000
    
    method = request.method
    path = request.path
    status_code = response.status_code
    content_length = response.content_length or 0
    
    # Log response
    http_logger.info(
        f"<<< RESPONSE | method={method} | path={path} | "
        f"status={status_code} | duration_ms={duration_ms:.0f} | "
        f"content_length={content_length}"
    )
    
    return response


# ========== ROUTES ==========

@app.route('/')
def index():
    app_logger.debug("Serving index page")
    pi_ip = os.environ["PI_IP_TS"]
    return render_template('index.html', pi_ip=pi_ip, privacy=is_privacy_on())


@app.route('/privacy-image')
def privacy_image():
    app_logger.debug("Serving privacy image")
    return send_file('./privacy.png', mimetype='image/png')


@app.route('/api/privacy/status')
def privacy_status():
    status = is_privacy_on()
    app_logger.debug(f"Privacy status requested: {status}")
    return jsonify({"privacy": status})


@app.route('/api/privacy/on', methods=['POST'])
def privacy_on():
    global _privacy_enabled
    _privacy_enabled = True
    privacy_logger.info("ENABLED | Privacy mode turned ON")
    app_logger.info("Privacy mode enabled")
    return jsonify({"status": "success", "privacy": True})


@app.route('/api/privacy/off', methods=['POST'])
def privacy_off():
    global _privacy_enabled
    _privacy_enabled = False
    privacy_logger.info("DISABLED | Privacy mode turned OFF")
    app_logger.info("Privacy mode disabled")
    return jsonify({"status": "success", "privacy": False})


@app.route('/api/move/start/<direction>')
def move_start(direction: str):
    if is_privacy_on():
        app_logger.warning(f"PTZ move blocked (privacy active): direction={direction}")
        http_logger.info(f"!!! BLOCKED | PTZ move | direction={direction} | reason=privacy_active")
        return jsonify({"status": "error", "message": "Privacy mode is active"}), 403
    
    direction = direction.lower()
    if direction not in DIRECTION_MAP:
        app_logger.warning(f"Invalid direction requested: {direction}")
        return jsonify({"status": "error", "message": f"Unknown direction: {direction}"}), 400
    
    ok = ptz_command("start", DIRECTION_MAP[direction])
    if not ok:
        return jsonify({"status": "error", "message": "Camera command failed"}), 502
    
    return jsonify({"status": "success", "action": "start", "direction": direction})


@app.route('/api/move/stop/<direction>')
def move_stop(direction: str):
    if is_privacy_on():
        app_logger.warning(f"PTZ move blocked (privacy active): direction={direction}")
        http_logger.info(f"!!! BLOCKED | PTZ move | direction={direction} | reason=privacy_active")
        return jsonify({"status": "error", "message": "Privacy mode is active"}), 403
    
    direction = direction.lower()
    if direction not in DIRECTION_MAP:
        app_logger.warning(f"Invalid direction requested: {direction}")
        return jsonify({"status": "error", "message": f"Unknown direction: {direction}"}), 400
    
    ok = ptz_command("stop", DIRECTION_MAP[direction])
    if not ok:
        return jsonify({"status": "error", "message": "Camera command failed"}), 502
    
    return jsonify({"status": "success", "action": "stop", "direction": direction})


@app.route('/api/home')
def home_camera():
    if is_privacy_on():
        app_logger.warning("Home command blocked (privacy active)")
        http_logger.info("!!! BLOCKED | Home command | reason=privacy_active")
        return jsonify({"status": "error", "message": "Privacy mode is active"}), 403
    
    ok = ptz_preset(1)
    if not ok:
        return jsonify({"status": "error", "message": "Home preset failed"}), 502
    
    return jsonify({"status": "success", "action": "home"})


# ========== ERROR HANDLING ==========

@app.errorhandler(404)
def not_found(error):
    app_logger.warning(f"404 Not Found: {request.path}")
    return jsonify({"status": "error", "message": "Not found"}), 404


@app.errorhandler(500)
def internal_error(error):
    app_logger.error(f"500 Internal Server Error: {str(error)}")
    return jsonify({"status": "error", "message": "Internal server error"}), 500


if __name__ == "__main__":
    try:
        app_logger.info("Starting Flask server on 0.0.0.0:1122")
        app.run(host='0.0.0.0', port=1122, debug=False)
    except KeyboardInterrupt:
        app_logger.info("Shutdown signal received - exiting gracefully")
    except Exception as e:
        app_logger.critical(f"Fatal error: {e}", exc_info=True)