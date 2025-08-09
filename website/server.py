
#!/usr/bin/env python3

from flask import Flask, render_template, request, jsonify, session
import os
from markupsafe import escape
import subprocess
import time

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
def api():
    action = request.form.get('action')

    print('Received action:', action)
    
    if action == 'start_microphone':
        offer = request.form.get('offer')
        name = request.form.get('name')

        print('Received offer:', offer)
        print('Received name:', name)
    
        if not offer:
            return jsonify({'success': False, 'error': 'Offer must not be empty'})
        if not name:
            return jsonify({'success': False, 'error': 'Name must not be empty'})
        
        # Here you would handle the offer and name, e.g., save them or process them
        # Start the webrtc-cli process if not already started
        webrtc_proc = 'webrtc_proc_' + name
        if not webrtc_proc in webrtc_procs or webrtc_procs[webrtc_proc].poll() is not None:
            webrtc_procs[webrtc_proc] = subprocess.Popen(
                ['webrtc-cli', 
                '--answer', 
                '--sink', 'virt-mic-1-sink',
                '--mode', 'lowdelay',
                '--rate', '48000',
                '--pulse-buf', '5ms',
                '--sink-frame', '5ms',
                '--jitter-buf', '5ms',
                '--max-drift', '5ms',
                '--chans', '2'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )

            print('Started webrtc-cli process for:', name)

            # Pipe the offer into the process
            webrtc_procs[webrtc_proc].stdin.write(offer)
            webrtc_procs[webrtc_proc].stdin.flush()
            webrtc_procs[webrtc_proc].stdin.close()

            print('Sent offer to webrtc-cli process')

            time.sleep(1)  # wait for the process to start and respond

            # Check if the process is still running
            if webrtc_procs[webrtc_proc].poll() is not None:
                while True:
                    line = webrtc_procs[webrtc_proc].stdout.readline()
                    print(line, end='')  # print the line to console
                    if not line:
                        break
                while True:
                    line = webrtc_procs[webrtc_proc].stderr.readline()
                    print(line, end='')  # print the line to console
                    if not line:
                        break

                return jsonify({'success': False, 'error': 'WebRTC process failed to start'})

            # read anwser from the process
            answer_lines = []
            while True:
                line = webrtc_procs[webrtc_proc].stdout.readline()
                print(line, end='')  # print the line to console
                if not line:
                    if len(answer_lines) == 0:
                        time.sleep(0.1)
                    else:
                        break
                answer_lines.append(line)

            answer = ''.join(answer_lines)
            webrtc_procs[webrtc_proc].stdout.close()

            print('Received answer from webrtc-cli process:', answer)

            return jsonify({'success': True, 'answer': answer})

        else:
            return jsonify({'success': False, 'error': 'WebRTC process is already running'})
    
    return jsonify({'success': False, 'error': 'Invalid action'})


@app.route('/static/<path:path>')   # serve static files
def static_files(path):
    return app.send_static_file(path)

if __name__ == '__main__':
    # Set the port to 5000 or any other port you prefer
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)