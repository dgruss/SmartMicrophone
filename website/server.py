
#!/usr/bin/env python3

from flask import Flask, render_template, request, jsonify, session, send_file, send_from_directory
import os
from markupsafe import escape
import random
import time
import logging
import signal
from webrtc_microphone import WebRTCMicrophone, WebRTCMicrophoneManager
import subprocess
import json

logger = logging.getLogger(__name__)

sessions = {}
microphone_assignments = [None] * 6  # 6 microphones: Blue, Red, Green, Orange, Yellow, Pink
remote_control_user = ""  # empty string means free
remote_control_text = ""

# In-memory songs index (populated at startup or on demand)
SONGS_LIST = None   # list of song entries
SONGS_BY_ID = {}    # map id (str) -> entry

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'eeWeidai3oSui8aike9vahyoh6kif2Uu')


@app.before_request
def log_incoming_request():
    try:
        logger.info('Incoming request: %s %s args=%s', request.method, request.path, dict(request.args))
    except Exception:
        pass

@app.route('/', methods=['GET']) # index route
def index():
    global automatically_reconnect
    
    automatically_reconnect = False

    # maybe this person just reloaded but still has a microphone running
    if 'session_id' in session and is_youngest_session():
        mic_idx = session.get('microphone_index')
        logger.info("Session %s is still active, automatically reconnecting microphone %s.", session.get('session_id'), mic_idx)
        # only reconnect automatically if we actually have a microphone index in the session
        if mic_idx is not None:
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


# Control tab: single-user lock and key sending via xdotool (if available)
CONTROL_OWNER = None       # session id who currently owns the control
CONTROL_OWNER_NAME = None  # human name for display
CONTROL_TIMESTAMP = 0
ULTRASTAR_WINDOW_ID = 0    # cached window id for UltraStar (0 = unknown/not found)

def run_xdotool_command(args):
    """Run xdotool and always target the UltraStar window.

    The UltraStar window id is discovered on first use via:
        xdotool search UltraStar
    The id is cached in ULTRASTAR_WINDOW_ID. If not found, return an error.
    `args` is a list of xdotool arguments (e.g. ['type', '--delay', '0', 'text']).
    """
    try:
        # check xdotool first
        which = subprocess.run(['which', 'xdotool'], capture_output=True, text=True)
        if which.returncode != 0 or not which.stdout.strip():
            logger.warning('xdotool not found on system; control commands will be logged but not sent')
            return False, 'xdotool not installed'

        # Prepare command args
        if isinstance(args, dict):
            cmd_args = list(args.get('args', []))
        else:
            cmd_args = list(args)

        # Ensure we have cached UltraStar window id
        global ULTRASTAR_WINDOW_ID
        if not ULTRASTAR_WINDOW_ID:
            try:
                # Use the simpler search as requested: `xdotool search UltraStar`
                ws = subprocess.run(['xdotool', 'search', 'UltraStar'], capture_output=True, text=True)
                ids = [l.strip() for l in ws.stdout.splitlines() if l.strip()]
                if ids:
                    ULTRASTAR_WINDOW_ID = ids[0]
                    logger.info('Cached UltraStar window id: %s', ULTRASTAR_WINDOW_ID)
                else:
                    logger.warning('No UltraStar window found via `xdotool search UltraStar`')
                    return False, 'window not found'
            except Exception as e:
                logger.exception('Error searching for UltraStar window: %s', e)
                return False, str(e)

        # construct full command, inserting --window <id> after the subcommand
        # args are expected like ['key', 'BackSpace'] or ['type', '--delay', '0', 'text']
        if not cmd_args:
            logger.warning('run_xdotool_command called with empty args')
            return False, 'empty args'
        subcmd = cmd_args[0]
        rest = cmd_args[1:]
        full_cmd = ['xdotool', subcmd, '--window', str(ULTRASTAR_WINDOW_ID)] + rest

        proc2 = subprocess.run(full_cmd, capture_output=True, text=True)
        if proc2.returncode != 0:
            logger.warning('xdotool failed: %s %s', full_cmd, proc2.stderr)
            return False, proc2.stderr
        return True, proc2.stdout
    except Exception as e:
        logger.exception('Error running xdotool: %s', e)
        return False, str(e)


@app.route('/control/status', methods=['GET'])
def control_status():
    global CONTROL_OWNER, CONTROL_OWNER_NAME, CONTROL_TIMESTAMP
    return jsonify({'owner': CONTROL_OWNER, 'owner_name': CONTROL_OWNER_NAME, 'timestamp': CONTROL_TIMESTAMP})


@app.route('/control/acquire', methods=['POST'])
def control_acquire():
    global CONTROL_OWNER, CONTROL_OWNER_NAME, CONTROL_TIMESTAMP
    data = request.get_json(force=True, silent=True) or {}
    name = data.get('name', '')
    sid = session.get('session_id')
    if not sid:
        # create a session id for controller
        sid = random.randint(1000000, 9999999)
        session['session_id'] = sid
    if CONTROL_OWNER and CONTROL_OWNER != sid:
        return jsonify({'success': False, 'error': 'Control already taken', 'owner': CONTROL_OWNER, 'owner_name': CONTROL_OWNER_NAME}), 409
    CONTROL_OWNER = sid
    CONTROL_OWNER_NAME = name or CONTROL_OWNER_NAME or 'Controller'
    CONTROL_TIMESTAMP = time.time()
    logger.info('Control acquired by %s (%s)', CONTROL_OWNER_NAME, CONTROL_OWNER)
    return jsonify({'success': True, 'owner': CONTROL_OWNER, 'owner_name': CONTROL_OWNER_NAME})


@app.route('/control/release', methods=['POST'])
def control_release():
    global CONTROL_OWNER, CONTROL_OWNER_NAME, CONTROL_TIMESTAMP
    sid = session.get('session_id')
    if not sid or CONTROL_OWNER != sid:
        return jsonify({'success': False, 'error': 'Not owner'}), 403
    CONTROL_OWNER = None
    CONTROL_OWNER_NAME = None
    CONTROL_TIMESTAMP = 0
    logger.info('Control released by session %s', sid)
    return jsonify({'success': True})


@app.route('/control/keystroke', methods=['POST'])
def control_keystroke():
    global CONTROL_OWNER
    data = request.get_json(force=True, silent=True) or {}
    key = data.get('key')
    sid = session.get('session_id')
    if not sid or CONTROL_OWNER != sid:
        return jsonify({'success': False, 'error': 'Not owner'}), 403
    if not key:
        return jsonify({'success': False, 'error': 'Missing key'}), 400

    # sanitize and map keys to xdotool names
    allowed_special = {
        'Escape': 'Escape', 'Esc': 'Escape', 'Enter': 'Return', 'Return': 'Return', 'Backspace': 'BackSpace',
        'Space': 'space', 'ArrowLeft': 'Left', 'ArrowRight': 'Right', 'ArrowUp': 'Up', 'ArrowDown': 'Down'
    }
    # If single printable character, send via type
    if len(key) == 1:
        ok, out = run_xdotool_command(['type', '--delay', '0', key])
        if not ok:
            return jsonify({'success': False, 'error': out}), 500
        return jsonify({'success': True})
    # map special
    mapped = allowed_special.get(key)
    if not mapped:
        return jsonify({'success': False, 'error': 'Unsupported key'}), 400
    ok, out = run_xdotool_command(['key', mapped])
    if not ok:
        return jsonify({'success': False, 'error': out}), 500
    return jsonify({'success': True})


@app.route('/control/text', methods=['POST'])
def control_text():
    global CONTROL_OWNER
    data = request.get_json(force=True, silent=True) or {}
    text = data.get('text', '')
    sid = session.get('session_id')
    if not sid or CONTROL_OWNER != sid:
        return jsonify({'success': False, 'error': 'Not owner'}), 403

    # strategy: send 20 backspaces then type the full text
    try:
        # send backspaces
        for _ in range(20):
            run_xdotool_command(['key', 'BackSpace'])
        # send the text
        if text:
            ok, out = run_xdotool_command(['type', '--delay', '0', text])
            if not ok:
                return jsonify({'success': False, 'error': out}), 500
        return jsonify({'success': True})
    except Exception as e:
        logger.exception('Error sending control text: %s', e)
        return jsonify({'success': False, 'error': str(e)}), 500


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


def scan_songs_and_build_index(find_root='../../usdx'):
    """Scan the given root for songs under any 'songs' directory and build a JSON index.

    The index will be written to website/data/songs_index.json (next to this server file).
    Each entry contains: txt (path), m4a (path), display (display name).
    """
    base_dir = os.path.dirname(__file__)
    data_dir = os.path.join(base_dir, 'data')
    os.makedirs(data_dir, exist_ok=True)
    paths_file = os.path.join(data_dir, 'song_txt_paths.txt')
    index_file = os.path.join(data_dir, 'songs_index.json')

    cmd = ['find', find_root, '-path', '*/songs/*', '-type', 'f', '-name', '*.txt']
    logger.info('Scanning songs with: %s', ' '.join(cmd))
    try:
        proc = subprocess.run(cmd, cwd=base_dir, capture_output=True, text=True, check=False)
        lines = [l.strip() for l in proc.stdout.splitlines() if l.strip()]
    except Exception as e:
        logger.exception('Song scan failed: %s', e)
        lines = []

    # write paths list
    try:
        with open(paths_file, 'w', encoding='utf-8') as fh:
            for p in lines:
                fh.write(p + '\n')
    except Exception:
        logger.exception('Failed to write %s', paths_file)

    entries = []
    for i, txtpath in enumerate(lines):
      m4apath = os.path.splitext(txtpath)[0] + '.m4a'
      display = os.path.splitext(os.path.basename(txtpath))[0].replace('_', ' ')
      entries.append({'id': i+1, 'txt': txtpath, 'm4a': m4apath, 'display': display, 'upl': False})

    try:
        with open(index_file, 'w', encoding='utf-8') as fh:
            json.dump(entries, fh, indent=2, ensure_ascii=False)
        logger.info('Wrote song index %s (%d entries)', index_file, len(entries))
    except Exception:
        logger.exception('Failed to write song index %s', index_file)
    # populate in-memory index
    try:
        global SONGS_LIST, SONGS_BY_ID
        SONGS_LIST = entries
        SONGS_BY_ID = {str(e['id']): e for e in entries if 'id' in e}
        logger.info('Populated in-memory songs index (%d entries)', len(SONGS_LIST))
    except Exception:
        logger.exception('Failed to populate in-memory songs index')


def load_songs_index():
    base_dir = os.path.dirname(__file__)
    index_file = os.path.join(base_dir, 'data', 'songs_index.json')
    try:
        with open(index_file, 'r', encoding='utf-8') as fh:
            items = json.load(fh)
            # populate in-memory index if not present
            global SONGS_LIST, SONGS_BY_ID
            SONGS_LIST = items
            SONGS_BY_ID = {str(e.get('id')): e for e in items if 'id' in e}
            return items
    except Exception:
        logger.exception('Failed to load song index %s', index_file)
        return []


@app.route('/songs/index', methods=['GET'])
def songs_index():
    return jsonify({'success': True, 'count': len(load_songs_index()), 'items': load_songs_index()})


@app.route('/songs/search', methods=['GET'])
def songs_search():
    q = request.args.get('q', '').strip().lower()
    page = int(request.args.get('page', '1'))
    per_page = int(request.args.get('per_page', '50'))
    items = load_songs_index()
    if q:
        items = [it for it in items if q in it.get('display','').lower()]
    total = len(items)
    start = (page-1)*per_page
    end = start + per_page
    page_items = items[start:end]
    return jsonify({'success': True, 'q': q, 'page': page, 'per_page': per_page, 'total': total, 'items': page_items})


@app.route('/songs/add_to_upl', methods=['POST'])
def songs_add_to_upl():
    # Accepts JSON body with 'id' and optional 'action' ('add'|'remove') to toggle presence in SmartMicSession.upl
    data = request.get_json(force=True, silent=True) or {}
    id_param = data.get('id')
    action = data.get('action', 'add')
    if not id_param:
        return jsonify({'success': False, 'error': 'Missing id'}), 400

    try:
        global SONGS_LIST, SONGS_BY_ID
        if not SONGS_BY_ID:
            load_songs_index()

        entry = SONGS_BY_ID.get(str(id_param))
        if not entry:
            return jsonify({'success': False, 'error': 'Not found', 'id': id_param}), 404

        # derive line (Artist : Title) from entry by reading the .txt file and looking for #ARTIST and #TITLE
        line = None
        try:
            txt_rel = entry.get('txt')
            if txt_rel:
                candidate_txt = os.path.realpath(os.path.join(os.path.dirname(__file__), txt_rel))
                # ensure txt is inside allowed root (same root used for previews)
                allowed_root = os.path.realpath(os.path.join(os.path.dirname(__file__), '../../usdx'))
                if candidate_txt.startswith(allowed_root) and os.path.exists(candidate_txt):
                    artist = None
                    title = None
                    try:
                        with open(candidate_txt, 'r', encoding='utf-8', errors='ignore') as fh:
                            for ln in fh:
                                s = ln.strip()
                                if not s:
                                    continue
                                up = s.upper()
                                if up.startswith('#ARTIST'):
                                    parts = s.split(':', 1)
                                    artist = parts[1].strip() if len(parts) > 1 else s[len('#ARTIST'):].strip()
                                elif up.startswith('#TITLE'):
                                    parts = s.split(':', 1)
                                    title = parts[1].strip() if len(parts) > 1 else s[len('#TITLE'):].strip()
                                if artist and title:
                                    break
                    except Exception:
                        logger.exception('Failed to read txt file for id %s: %s', id_param, candidate_txt)
                    if artist or title:
                        # build line with available parts
                        if artist and title:
                            line = f"{artist} : {title}"
                        elif artist:
                            line = artist
                        else:
                            line = title
        except Exception:
            logger.exception('Error deriving artist/title for id %s', id_param)

        if not line:
            line = entry.get('display') or os.path.splitext(os.path.basename(entry.get('txt','')))[0].replace('_',' ')
        upl_path = os.path.realpath(os.path.join(os.path.dirname(__file__), '../../usdx/playlists', 'SmartMicSession.upl'))

        # Ensure upl file exists
        try:
            if not os.path.exists(upl_path):
                open(upl_path, 'a', encoding='utf-8').close()
        except Exception:
            logger.exception('Failed to ensure upl file exists: %s', upl_path)

        if action == 'add':
            # append only if not already present
            existing = []
            try:
                with open(upl_path, 'r', encoding='utf-8') as fh:
                    existing = [l.strip() for l in fh if l.strip()]
            except Exception:
                existing = []
            if line not in existing:
                try:
                    with open(upl_path, 'a', encoding='utf-8') as fh:
                        fh.write(line + '\n')
                except Exception as e:
                    logger.exception('Failed to append to upl %s', upl_path)
                    return jsonify({'success': False, 'error': str(e)}), 500
            entry['upl'] = True

        elif action == 'remove':
            # remove matching lines from upl file
            try:
                if os.path.exists(upl_path):
                    with open(upl_path, 'r', encoding='utf-8') as fh:
                        lines = [l.rstrip('\n') for l in fh]
                    newlines = [l for l in lines if l.strip() != line]
                    with open(upl_path, 'w', encoding='utf-8') as fh:
                        for l in newlines:
                            fh.write(l + '\n')
            except Exception as e:
                logger.exception('Failed to remove from upl %s', upl_path)
                return jsonify({'success': False, 'error': str(e)}), 500
            entry['upl'] = False

        else:
            return jsonify({'success': False, 'error': 'Unknown action'}), 400

        # persist updated index to disk
        try:
            base_dir = os.path.dirname(__file__)
            index_file = os.path.join(base_dir, 'data', 'songs_index.json')
            with open(index_file, 'w', encoding='utf-8') as fh:
                json.dump(SONGS_LIST, fh, indent=2, ensure_ascii=False)
        except Exception:
            logger.exception('Failed to persist song index after upl change')

        # update in-memory map
        SONGS_BY_ID[str(entry.get('id'))] = entry

        return jsonify({'success': True, 'id': entry.get('id'), 'upl': entry.get('upl', False), 'line': line})

    except Exception as e:
        logger.exception('Failed to modify upl file')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/songs/preview')
def songs_preview():
    # preview m4a path passed as query param 'path' (the path as stored in index)
    try:
        # ID-based preview only: client must supply ?id=<id>
        id_param = request.args.get('id')
        base_dir = os.path.dirname(__file__)
        allowed_root = os.path.realpath(os.path.join(base_dir, '../../usdx'))

        if not id_param:
            logger.info('Preview called without id')
            return jsonify({'success': False, 'error': 'Missing id'}), 400

        try:
            global SONGS_BY_ID
            found = None
            if SONGS_BY_ID and str(id_param) in SONGS_BY_ID:
                found = SONGS_BY_ID.get(str(id_param))
            else:
                # fallback: load index and rebuild mapping
                items = load_songs_index()
                found = None
                for it in items:
                    if 'id' in it and str(it.get('id')) == str(id_param):
                        found = it
                        break
                # repopulate in-memory map
                try:
                    SONGS_BY_ID = {str(e.get('id')): e for e in items if 'id' in e}
                except Exception:
                    pass

            if not found:
                logger.warning('Preview id not found: %s', id_param)
                return jsonify({'success': False, 'error': 'Not found', 'id': id_param}), 404

            m4apath = found.get('m4a')
            candidate = os.path.realpath(os.path.join(base_dir, m4apath))
            logger.info('Preview by id=%s resolved to %s', id_param, candidate)
        except Exception as e:
            logger.exception('Error resolving preview id %s: %s', id_param, e)
            return jsonify({'success': False, 'error': 'Server error', 'detail': str(e)}), 500

        logger.info('Preview request candidate=%s allowed_root=%s', candidate, allowed_root)

        if not candidate.startswith(allowed_root):
            logger.warning('Preview request outside allowed root: %s', candidate)
            return jsonify({'success': False, 'error': 'Forbidden'}), 403

        if not os.path.exists(candidate):
            logger.warning('Preview candidate not found: %s', candidate)
            return jsonify({'success': False, 'error': 'Not found', 'path': candidate}), 404

        return send_file(candidate)
    except Exception as e:
        logger.exception('Error handling preview request: %s', e)
        return jsonify({'success': False, 'error': 'Server error', 'detail': str(e)}), 500



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

    # Build/update song index at startup
    try:
        scan_songs_and_build_index()
    except Exception:
        logger.exception('Error scanning songs at startup')

    # Ensure SmartMicSession.upl exists and is truncated at startup
    try:
        base_dir = os.path.dirname(__file__)
        upl_dir = os.path.realpath(os.path.join(base_dir, '../../usdx/playlists'))
        os.makedirs(upl_dir, exist_ok=True)
        upl_path = os.path.join(upl_dir, 'SmartMicSession.upl')
        # Truncate/create the file
        with open(upl_path, 'w', encoding='utf-8') as fh:
            fh.truncate(0)
        logger.info('Initialized SmartMicSession.upl at %s', upl_path)
    except Exception:
        logger.exception('Failed to create/truncate SmartMicSession.upl')

    # Set the port to 5000 or any other port you prefer
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True, use_reloader=False, ssl_context=("../../fullchain.pem", "../../privkey.pem"))
