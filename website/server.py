
#!/usr/bin/env python3

from flask import Flask, render_template, request, jsonify, session
import os
from markupsafe import escape
import random
import time
import logging
import signal
from webrtc_microphone import WebRTCMicrophone, WebRTCMicrophoneManager

logger = logging.getLogger(__name__)

sessions = {}

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'eeWeidai3oSui8aike9vahyoh6kif2Uu')

@app.route('/', methods=['GET']) # index route
def index():
    global automatically_reconnect
    
    automatically_reconnect = False

    # maybe this person just reloaded but still has a microphone running
    if 'session_id' in session and is_youngest_session():
        logger.info(f"Session {session['session_id']} is still active, automatically reconnecting microphone {session['microphone_index']}.")
        # this session is the youngest, automatically reconnect the microphone
        automatically_reconnect = True

    return render_template('index.html')


@app.route('/api', methods=['POST'])
def api():
    action = request.form.get('action')

    logger.info(f'Received action: {action}')
    
    if action == 'start_microphone':
        offer = request.form.get('offer')

        if 'session_id' in session and is_youngest_session():
            response = WebRTCMicrophoneManager().start_microphone(offer, session.get('microphone_index', -1))

        else:
            response = WebRTCMicrophoneManager().start_microphone(offer)

        if response.get('success'):
            session['microphone_index'] = response.get('index')
            session['microphone_start_timestamp'] = time.time()
            session['session_id'] = random.randint(0, 9999999)

            sessions[session['session_id']] = {
                'microphone_index': session['microphone_index'],
                'microphone_start_timestamp': session['microphone_start_timestamp']
            }

        return jsonify(response)
    
    elif action == 'stop_microphone':
        if 'session_id' in session and is_youngest_session():
            response = WebRTCMicrophoneManager().stop_microphone(session.get('microphone_index', -1))

            if response.get('success'):
                session.pop('microphone_index', None)
                session.pop('microphone_start_timestamp', None)
                session.pop('session_id', None)
                
                if 'microphone_index' in sessions:
                    del sessions[session['session_id']]

            return response
        
        return jsonify({'success': False, 'error': 'Invalid session'})

    return jsonify({'success': False, 'error': 'Invalid action'})


@app.route('/static/<path:path>')   # serve static files
def static_files(path):
    return app.send_static_file(path)


@app.context_processor
def inject_stage_and_region():
    return dict(automatically_reconnect=automatically_reconnect)


def is_youngest_session():
    youngest_session = True

    for session_id, session_data in sessions.items():
        if session_id == session.get('session_id'):
            continue

        if session_data['microphone_index'] == session.get('microphone_index', -1):
            if session_data['microphone_start_timestamp'] > session.get('microphone_start_timestamp', 0):
                # this session is older, so we can continue
                youngest_session = False

    return youngest_session


def signal_handler(signum, frame):
    print(f"Received signal {signum}, shutting down gracefully...")
    WebRTCMicrophoneManager().stop()
    print("All microphones stopped. Exiting now.")

    #time.sleep(0.1)  # Give some time for cleanup
    raise RuntimeError("Server going down")


if __name__ == '__main__':
    signal.signal(signal.SIGINT, signal_handler)

    logging.basicConfig(filename='virtual-microphone.log', level=logging.INFO)

    WebRTCMicrophoneManager().init()

    # Set the port to 5000 or any other port you prefer
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True, use_reloader=False)