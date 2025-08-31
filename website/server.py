
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
microphone_assignments = [None] * 6  # 6 microphones: Blue, Red, Green, Orange, Yellow, Pink
remote_control_user = ""  # empty string means free
remote_control_text = ""

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
    global remote_control_text, remote_control_user
    action = request.form.get('action')
    logger.info(f'Received action: {action}')

    if action == 'start_microphone':
        offer = request.form.get('offer')
        mic_index = int(request.form.get('index', -1))

        if mic_index < 0 or mic_index >= len(microphone_assignments):
            return jsonify({'success': False, 'error': 'Invalid microphone index'})

        # Assign microphone to this session
        if microphone_assignments[mic_index] is None or microphone_assignments[mic_index] == session.get('session_id'):
            response = WebRTCMicrophoneManager().start_microphone(offer, mic_index)
            if response.get('success'):
                session['microphone_index'] = mic_index
                session['microphone_start_timestamp'] = time.time()
                session['session_id'] = session.get('session_id', random.randint(0, 9999999))
                sessions[session['session_id']] = {
                    'microphone_index': mic_index,
                    'microphone_start_timestamp': session['microphone_start_timestamp']
                }
                microphone_assignments[mic_index] = session['session_id']
                response['assignments'] = get_mic_assignments()
            return jsonify(response)
        else:
            return jsonify({'success': False, 'error': 'Microphone already in use'})

    elif action == 'stop_microphone':
        mic_index = session.get('microphone_index', -1)
        if 'session_id' in session and is_youngest_session():
            response = WebRTCMicrophoneManager().stop_microphone(mic_index)
            if response.get('success'):
                session.pop('microphone_index', None)
                session.pop('microphone_start_timestamp', None)
                session.pop('session_id', None)
                if mic_index >= 0 and mic_index < len(microphone_assignments):
                    microphone_assignments[mic_index] = None
            response['assignments'] = get_mic_assignments()
            return jsonify(response)
        return jsonify({'success': False, 'error': 'Invalid session'})

    elif action == 'select_microphone':
        mic_index = int(request.form.get('index', -1))
        if mic_index < 0 or mic_index >= len(microphone_assignments):
            return jsonify({'success': False, 'error': 'Invalid microphone index'})
        session['microphone_index'] = mic_index
        return jsonify({'success': True, 'assignments': get_mic_assignments()})

    elif action == 'get_assignments':
        return jsonify({'success': True, 'assignments': get_mic_assignments()})

    elif action == 'remote_text':
        if remote_control_user == "" or remote_control_user == session.get('session_id'):
            remote_control_user = session.get('session_id')
            remote_control_text = request.form.get('text', '')
            return jsonify({'success': True, 'user': remote_control_user, 'text': remote_control_text})
        else:
            return jsonify({'success': False, 'error': f'Remote control in use by {remote_control_user}'})

    elif action == 'remote_command':
        cmd = request.form.get('command')
        if remote_control_user == "" or remote_control_user == session.get('session_id'):
            remote_control_user = session.get('session_id')
            # Here you would process the command (enter, up, down, left, right)
            logger.info(f'Remote command: {cmd} by {remote_control_user}')
            return jsonify({'success': True, 'user': remote_control_user, 'command': cmd})
        else:
            return jsonify({'success': False, 'error': f'Remote control in use by {remote_control_user}'})

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

    def get_mic_assignments():
        # Returns a list of user display names or None for each mic
        result = []
        for sid in microphone_assignments:
            if sid is None:
                result.append(None)
            else:
                # For demo, just show session id
                result.append(str(sid))
        return result


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
    app.run(host='0.0.0.0', port=port, debug=True, use_reloader=False, ssl_context=("../../fullchain.pem", "../../privkey.pem"))
