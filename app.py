import os
from flask import Flask, render_template

app = Flask(__name__)

@app.route('/')
def index():
    # We pass the Pi's IP so the frontend knows where the streamer is.
    # Replace this with your actual Pi IP or Tailscale address.
    pi_ip = os.getenv("PI_IP_TS", "localhost") 
    return render_template('index.html', pi_ip=pi_ip)

@app.route('/api/move/<direction>')
def move_camera(direction):
    # This is where your GPIO code will go later this week!
    print(f"WEB COMMAND: Moving camera {direction.upper()}")
    return {"status": "success", "direction": direction}

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=1122, debug=False)