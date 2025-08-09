
#!/usr/bin/env python3

from flask import Flask, render_template, request, jsonify, session
import os
from markupsafe import escape
import subprocess
import time
import logging
import signal
from webrtc_microphone import WebRTCMicrophone, WebRTCMicrophoneManager

logger = logging.getLogger(__name__)

webrtc_procs = {}

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'eeWeidai3oSui8aike9vahyoh6kif2Uu')

@app.route('/', methods=['GET', 'POST']) # index route
def index():
    # Session management logic
    if 'name' not in session:
        name = request.form.get('name')
        print('Name from request:', name)
        if name:
            session['name'] = escape(name)
            name = session['name']
    else:
        if request.form.get('reset'):
            session.pop('name', None)
            name = None
        else:
            name = session['name']

    return render_template('index.html')


@app.route('/api', methods=['POST'])
async def api():
    action = request.form.get('action')

    logger.info(f'Received action: {action}')
    
    if action == 'start_microphone':
        offer = request.form.get('offer')
        name = request.form.get('name')

        response = await WebRTCMicrophoneManager().add_microphone(name, offer)

        return jsonify(response)
    
    return jsonify({'success': False, 'error': 'Invalid action'})


@app.route('/static/<path:path>')   # serve static files
def static_files(path):
    return app.send_static_file(path)


def signal_handler(signum, frame):
    print(f"Received signal {signum}, shutting down gracefully...")
    WebRTCMicrophoneManager().stop_all_microphones()
    print("All microphones stopped. Exiting now.")

    time.sleep(1)  # Give some time for cleanup
    raise RuntimeError("Server going down")


if __name__ == '__main__':
    signal.signal(signal.SIGINT, signal_handler)

    logging.basicConfig(filename='virtual-microphone.log', level=logging.INFO)

    # Set the port to 5000 or any other port you prefer
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)