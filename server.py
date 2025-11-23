#!/usr/bin/env python3

from flask import Flask, render_template, request, jsonify, session, send_file, send_from_directory
from flask import Response, stream_with_context
import os
from markupsafe import escape
import random
import time
import logging
import signal
from webrtc_microphone import WebRTCMicrophone, WebRTCMicrophoneManager
import subprocess
import json
import threading
import queue
import configparser
import argparse
import logging
import sys
import socket
import time


log = logging.getLogger('werkzeug')
log.setLevel(logging.WARNING)

class NoSpaceConfigParser(configparser.ConfigParser):
    def _write_section(self, fp, section_name, section_items, delimiter):
        fp.write(f"[{section_name}]\n")
        for key, value in section_items:
            if value is not None or not self.allow_no_value:
                value = str(value).replace('\n', '\n\t')
                fp.write(f"{key}={value}\n")
            else:
                fp.write(f"{key}\n")
        fp.write("\n")

logger = logging.getLogger(__name__)

sessions = {}
microphone_assignments = [None] * 6  # 6 microphones: Blue, Red, Green, Orange, Yellow, Pink
remote_control_user = ""  # empty string means free
remote_control_text = ""
# Track last-seen timestamp for each session id (heartbeat from clients)
LAST_SEEN = {}

# Global rooms mapping: room name -> list of usernames
ROOMS = {
    'lobby': [],
    'mic1': [],
    'mic2': [],
    'mic3': [],
    'mic4': [],
    'mic5': [],
    'mic6': []
}

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, 'data')
os.makedirs(DATA_DIR, exist_ok=True)
ROOM_CAPACITY_FILE = os.path.join(DATA_DIR, 'room_capacity.json')

# Default per-channel capacity (can be updated at runtime via API)
DEFAULT_ROOM_CAPACITY = {
    'mic1': 6,
    'mic2': 6,
    'mic3': 6,
    'mic4': 6,
    'mic5': 6,
    'mic6': 6
}
ROOM_CAPACITY = {}


def _normalize_capacity_value(value, fallback=6):
    try:
        val = int(value)
    except Exception:
        return fallback
    return max(1, min(6, val))


def load_room_capacity():
    """Load persisted room capacity limits from disk or fall back to defaults."""
    global ROOM_CAPACITY
    caps = {}
    try:
        with open(ROOM_CAPACITY_FILE, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
            if isinstance(data, dict):
                for room, value in data.items():
                    caps[room] = _normalize_capacity_value(value, DEFAULT_ROOM_CAPACITY.get(room, 6))
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.exception('Failed to load room capacity file: %s', exc)

    for room in ROOMS.keys():
        if room == 'lobby':
            continue
    caps.setdefault(room, DEFAULT_ROOM_CAPACITY.get(room, 6))

    ROOM_CAPACITY = caps
    return ROOM_CAPACITY


def save_room_capacity():
    try:
        with open(ROOM_CAPACITY_FILE, 'w', encoding='utf-8') as fh:
            json.dump(ROOM_CAPACITY, fh, indent=2, ensure_ascii=False)
        return True
    except Exception as exc:
        logger.exception('Failed to write room capacity file: %s', exc)
        return False


load_room_capacity()

# Optional mapping of session id -> username for quick lookup
SESSION_USERNAMES = {}
# Optional mapping of session id -> preferred per-player delay (ms)
SESSION_DELAYS = {}
# Optional mapping of session id -> most recent room selection
SESSION_ROOMS = {}

# SSE listeners will be set up after the Flask app is created to avoid
# referencing `app` before initialization.

# In-memory songs index (populated at startup or on demand)
SONGS_LIST = None   # list of song entries
SONGS_BY_ID = {}    # map id (str) -> entry

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'eeWeidai3oSui8aike9vahyoh6kif2Uu')

# SSE listeners for real-time room updates (list of queue.Queue)
ROOMS_LISTENERS = []
ROOMS_LISTENERS_LOCK = threading.Lock()


def notify_rooms_update():
    """Push the current rooms map to all SSE listeners."""
    payload = json.dumps({'rooms': ROOMS, 'capacity': ROOM_CAPACITY})
    with ROOMS_LISTENERS_LOCK:
        for q in list(ROOMS_LISTENERS):
            try:
                q.put(payload, block=False)
            except Exception:
                try:
                    q.put(payload)
                except Exception:
                    pass


@app.route('/rooms/stream')
def rooms_stream():
    """Server-Sent Events stream that emits room updates as JSON.

    Clients should connect with EventSource('/rooms/stream').
    """
    q = queue.Queue()
    with ROOMS_LISTENERS_LOCK:
        ROOMS_LISTENERS.append(q)

    def gen():
        # initial state
        try:
            yield f"data: {json.dumps({'rooms': ROOMS, 'capacity': ROOM_CAPACITY})}\n\n"
            while True:
                data = q.get()
                yield f"data: {data}\n\n"
        except GeneratorExit:
            # client disconnected
            return
        finally:
            # clean up listener
            with ROOMS_LISTENERS_LOCK:
                try:
                    ROOMS_LISTENERS.remove(q)
                except ValueError:
                    pass

    return Response(stream_with_context(gen()), mimetype='text/event-stream')

# Endpoint that merges rooms and control status and records a heartbeat
@app.route('/status', methods=['GET'])
def status():
    try:
        sid = session.get('session_id')
        if sid:
            LAST_SEEN[sid] = time.time()
        current_room = session.get('current_room')
        if sid and not current_room:
            current_room = SESSION_ROOMS.get(sid)
        user_payload = {
            'session_id': sid,
            'name': SESSION_USERNAMES.get(sid),
            'room': current_room
        }
        # prepare payload with rooms and control status
        payload = {
            'success': True,
            'rooms': {r: list(u) for r, u in ROOMS.items()},
            'capacity': dict(ROOM_CAPACITY),
            'control': {
                'owner': CONTROL_OWNER,
                'owner_name': CONTROL_OWNER_NAME,
                'timestamp': CONTROL_TIMESTAMP,
                'password_required': control_password_required(),
                'password_ok': control_password_ok_for_session()
            },
            'you': user_payload
        }
        return jsonify(payload)
    except Exception as e:
        logger.exception('Failed to serve /status: %s', e)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.before_request
def log_incoming_request():
    try:
        sid = session.get('session_id')
        if sid:
            LAST_SEEN[sid] = time.time()
        if not request.path in ['/rooms', '/status', '/control/status']:
            logger.info('Incoming request: %s %s args=%s', request.method, request.path, dict(request.args))
    except Exception:
        pass

@app.route('/', methods=['GET']) # index route
def index():
    global automatically_reconnect
    automatically_reconnect = False

    # On first visit, ensure session id
    player_id = session.get('session_id', None)
    if not player_id:
        player_id = random.randint(0, 9999999)
        session['session_id'] = player_id

    # maybe this person just reloaded but still has a microphone running
    if 'session_id' in session and is_youngest_session():
        mic_idx = session.get('microphone_index')
        logger.debug("Session %s is still active, automatically reconnecting microphone %s.", session.get('session_id'), mic_idx)
        # only reconnect automatically if we actually have a microphone index in the session
        if mic_idx is not None:
            automatically_reconnect = True

    return render_template('index.html')
@app.route('/api/disconnect', methods=['POST'])
def api_disconnect():
    # Called from JS on page close/unload to clean up the player's source
    player_id = session.get('session_id', None)
    if player_id:
        mgr = WebRTCMicrophoneManager()
        mgr.remove_microphone(player_id)
        session.pop('microphone_index', None)
        session.pop('microphone_start_timestamp', None)
        session.pop('session_id', None)
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'No session'})


@app.route('/api', methods=['POST'])
def api():
    global remote_control_text, remote_control_user
    action = request.form.get('action')
    logger.info(f'Received action: {action}')


    if action == 'start_webrtc':
        # Start per-player pulse-receive using the provided SDP offer
        offer = request.form.get('offer')
        if not offer:
            return jsonify({'success': False, 'error': 'Missing offer'})
        player_id = session.get('session_id')
        if not player_id:
            player_id = random.randint(0, 9999999)
            session['session_id'] = player_id

        mgr = WebRTCMicrophoneManager()
        start_res = mgr.start_microphone(player_id, offer)
        if not start_res.get('success'):
            return jsonify({'success': False, 'error': start_res.get('error', 'Failed to start webrtc')})

        # connect monitor to current sink (default lobby)
        sink_index = session.get('microphone_index', 0)
        try:
            mgr.connect_microphone_to_sink(player_id, sink_index)
        except Exception:
            pass

        # record session info
        session['microphone_index'] = sink_index
        session['microphone_start_timestamp'] = time.time()
        sessions[player_id] = {
            'microphone_index': sink_index,
            'microphone_start_timestamp': session['microphone_start_timestamp']
        }

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
    else:
        return jsonify({'success': False, 'error': 'Invalid action: ' + action})
    return jsonify({'success': True, 'answer': start_res.get('answer'), 'player_id': player_id})


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
CONTROL_PASSWORD = None    # optional password required before using control tab


def control_password_required():
    return bool(CONTROL_PASSWORD)


def control_password_ok_for_session():
    return (not CONTROL_PASSWORD) or session.get('control_password_ok') is True


def enforce_control_password():
    if not CONTROL_PASSWORD:
        return None
    if session.get('control_password_ok') is True:
        return None
    return jsonify({
        'success': False,
        'error': 'Control password required',
        'error_code': 'control_password_required'
    }), 403

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
                    logger.debug('Cached UltraStar window id: %s', ULTRASTAR_WINDOW_ID)
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

@app.route('/player/delay', methods=['POST'])
def player_delay():
    """Update the current player's delay (ms). JSON: {delay: <ms>}"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        delay_val = data.get('delay')
        if delay_val is None:
            return jsonify({'success': False, 'error': 'Missing delay'}), 400
        sid = session.get('session_id')
        if not sid:
            sid = random.randint(1000000, 9999999)
            session['session_id'] = sid
        try:
            SESSION_DELAYS[sid] = int(delay_val)
        except Exception:
            SESSION_DELAYS[sid] = 0
        try:
            update_config_players()
        except Exception:
            pass
        return jsonify({'success': True, 'delay': SESSION_DELAYS[sid]})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/control/auth', methods=['POST'])
def control_auth():
    global CONTROL_PASSWORD
    data = request.get_json(force=True, silent=True) or {}
    if not CONTROL_PASSWORD:
        session['control_password_ok'] = True
        return jsonify({'success': True, 'password_required': False, 'password_ok': True})
    provided = data.get('password', '')
    if isinstance(provided, str) and provided == CONTROL_PASSWORD:
        session['control_password_ok'] = True
        return jsonify({'success': True, 'password_required': True, 'password_ok': True})
    session['control_password_ok'] = False
    return jsonify({'success': False, 'error': 'Invalid control password', 'error_code': 'invalid_password'}), 403


@app.route('/control/status', methods=['GET'])
def control_status():
    global CONTROL_OWNER, CONTROL_OWNER_NAME, CONTROL_TIMESTAMP
    return jsonify({
        'owner': CONTROL_OWNER,
        'owner_name': CONTROL_OWNER_NAME,
        'timestamp': CONTROL_TIMESTAMP,
        'password_required': control_password_required(),
        'password_ok': control_password_ok_for_session()
    })


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
    guard = enforce_control_password()
    if guard is not None:
        return guard
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
    guard = enforce_control_password()
    if guard is not None:
        return guard
    CONTROL_OWNER = None
    CONTROL_OWNER_NAME = None
    CONTROL_TIMESTAMP = 0
    logger.debug('Control released by session %s', sid)
    return jsonify({'success': True})


@app.route('/control/keystroke', methods=['POST'])
def control_keystroke():
    global CONTROL_OWNER
    data = request.get_json(force=True, silent=True) or {}
    key = data.get('key')
    sid = session.get('session_id')
    if not sid or CONTROL_OWNER != sid:
        return jsonify({'success': False, 'error': 'Not owner'}), 403
    guard = enforce_control_password()
    if guard is not None:
        return guard
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
    guard = enforce_control_password()
    if guard is not None:
        return guard

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


def scan_songs_and_build_index(find_root=None):
    """Scan the given root for songs under any 'songs' directory and build a JSON index.

    The index will be written to website/data/songs_index.json (next to this server file).
    Each entry contains: txt (path), m4a (path), display (display name).
    """
    base_dir = os.path.dirname(__file__)
    data_dir = os.path.join(base_dir, 'data')
    os.makedirs(data_dir, exist_ok=True)
    paths_file = os.path.join(data_dir, 'song_txt_paths.txt')
    index_file = os.path.join(data_dir, 'songs_index.json')

    if find_root is None:
        find_root = args.usdx_dir
    cmd = ['find', '-L', find_root, '-path', '*/songs/*', '-type', 'f', '-name', '*.txt']
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
        audio_ext = args.audio_format if 'args' in globals() else 'm4a'
        audio_path = os.path.splitext(txtpath)[0] + f'.{audio_ext}'
        display = os.path.splitext(os.path.basename(txtpath))[0].replace('_', ' ')
        entries.append({'id': i+1, 'txt': txtpath, audio_ext: audio_path, 'display': display, 'upl': False})

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


@app.route('/rooms', methods=['GET'])
def rooms_list():
    """Return the current rooms mapping (room -> list of usernames)."""
    try:
        # Return a shallow copy to avoid accidental modifications by client
        return jsonify({'success': True, 'rooms': {r: list(u) for r, u in ROOMS.items()}, 'capacity': dict(ROOM_CAPACITY)})
    except Exception as e:
        logger.exception('Failed to list rooms: %s', e)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/rooms/capacity', methods=['GET'])
def rooms_capacity_get():
    try:
        return jsonify({'success': True, 'capacity': dict(ROOM_CAPACITY)})
    except Exception as e:
        logger.exception('Failed to get room capacity: %s', e)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/rooms/capacity', methods=['POST'])
def rooms_capacity_set():
    """Update one or more room capacities. JSON: {room: 'mic1', limit: 3} or {capacity: {mic1:3}}"""
    try:
        global CONTROL_OWNER
        sid = session.get('session_id')
        if not sid or CONTROL_OWNER != sid:
            return jsonify({'success': False, 'error': 'Control lock required to change capacity', 'error_code': 'control_required'}), 403
        data = request.get_json(force=True, silent=True) or {}
        updates = {}
        if 'capacity' in data and isinstance(data['capacity'], dict):
            updates = data['capacity']
        else:
            room = data.get('room')
            limit = data.get('limit')
            if room:
                updates = {room: limit}

        if not updates:
            return jsonify({'success': False, 'error': 'No capacity updates provided'}), 400

        changed = {}
        for room, value in updates.items():
            if room not in ROOMS or room == 'lobby':
                continue
            if value is None:
                continue
            ROOM_CAPACITY[room] = _normalize_capacity_value(value, DEFAULT_ROOM_CAPACITY.get(room, 2))
            changed[room] = ROOM_CAPACITY[room]

        if not changed:
            return jsonify({'success': False, 'error': 'No valid rooms to update'}), 400

        save_room_capacity()
        try:
            notify_rooms_update()
        except Exception:
            pass
        return jsonify({'success': True, 'capacity': dict(ROOM_CAPACITY)})
    except Exception as e:
        logger.exception('Failed to set room capacity: %s', e)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/rooms/join', methods=['POST'])
def rooms_join():
    """Join a room. JSON: {room: 'lobby'|'mic1'|..., name: 'displayname'}"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        room = data.get('room', 'lobby')
        name = data.get('name', '')
        if room not in ROOMS:
            return jsonify({'success': False, 'error': 'Unknown room'}), 400

        # ensure session id
        sid = session.get('session_id')
        if not sid:
            sid = random.randint(1000000, 9999999)
            session['session_id'] = sid

        # record username and optional delay for session
        username = str(name) if name else SESSION_USERNAMES.get(sid, f'user-{sid}')
        SESSION_USERNAMES[sid] = username
        # allow the client to submit a per-player delay (ms)
        try:
            delay_val = data.get('delay')
            if delay_val is not None:
                try:
                    SESSION_DELAYS[sid] = int(delay_val)
                except Exception:
                    SESSION_DELAYS[sid] = 0
        except Exception:
            pass

        # Remove from any other rooms first
        for r, users in ROOMS.items():
            ROOMS[r] = [u for u in users if u != username]

        # Enforce capacity for mic rooms (lobby is unlimited)
        if room != 'lobby':
            limit = ROOM_CAPACITY.get(room, DEFAULT_ROOM_CAPACITY.get(room, 2))
            if limit and len(ROOMS[room]) >= limit:
                rooms_snapshot = {r: list(u) for r, u in ROOMS.items()}
                return jsonify({
                    'success': False,
                    'error': f'{room} is full',
                    'error_code': 'room_full',
                    'room': room,
                    'members': len(ROOMS[room]),
                    'capacity': limit,
                    'rooms': rooms_snapshot,
                    'capacity_map': dict(ROOM_CAPACITY)
                }), 409

        # Add to target room
        ROOMS[room].append(username)
        SESSION_ROOMS[sid] = room
        session['current_room'] = room

        # Connect the player's source to the correct sink if their microphone is running
        mgr = WebRTCMicrophoneManager()
        # Map room name to sink index: 'lobby' -> 0, 'mic1' -> 1, ...
        sink_index = 0
        if room.startswith('mic'):
            try:
                sink_index = int(room[3:])
            except Exception:
                sink_index = 0
        # Only attempt to connect if a microphone/process exists for this session
        try:
            if sid in mgr.microphones:
                mgr.connect_microphone_to_sink(sid, sink_index)
        except Exception:
            logger.exception('Failed to connect microphone for session %s to sink %s', sid, sink_index)
            pass

        try:
            session['microphone_index'] = sink_index
            if sid in sessions:
                sessions[sid]['microphone_index'] = sink_index
        except Exception:
            logger.exception('Failed to persist sink index for session %s', sid)

        # Notify SSE listeners of update
        try:
            notify_rooms_update()
        except Exception:
            pass
        # Update external config.ini player list
        try:
            update_config_players()
        except Exception:
            pass
        logger.info('Session %s joined room %s as %s', sid, room, username)
        return jsonify({
            'success': True,
            'room': room,
            'name': username,
            'rooms': {r: list(u) for r, u in ROOMS.items()},
            'capacity': dict(ROOM_CAPACITY)
        })
    except Exception as e:
        logger.exception('Failed to join room: %s', e)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/rooms/leave', methods=['POST'])
def rooms_leave():
    """Leave the current room for this session. JSON: {name: 'displayname'} (name optional)."""
    try:
        data = request.get_json(force=True, silent=True) or {}
        name = data.get('name', None)

        sid = session.get('session_id')
        if not sid and not name:
            return jsonify({'success': False, 'error': 'No session or name provided'}), 400

        username = None
        if name:
            username = str(name)
        else:
            username = SESSION_USERNAMES.get(sid)

        if not username:
            return jsonify({'success': False, 'error': 'Unknown user'}), 400

        # Remove user from all rooms
        for r, users in ROOMS.items():
            ROOMS[r] = [u for u in users if u != username]

        # Optionally remove session->username mapping
        if sid and sid in SESSION_USERNAMES:
            try:
                del SESSION_USERNAMES[sid]
            except Exception:
                pass
        if sid:
            SESSION_ROOMS.pop(sid, None)
        try:
            session.pop('current_room', None)
        except Exception:
            pass
    except Exception as e:
        logger.exception('Failed to leave room: %s', e)
        return jsonify({'success': False, 'error': str(e)}), 500
    logger.info('User %s left all rooms', username)
    try:
        notify_rooms_update()
    except Exception:
        pass
    try:
        update_config_players()
    except Exception:
        pass
    return jsonify({'success': True, 'rooms': {r: list(u) for r, u in ROOMS.items()}, 'capacity': dict(ROOM_CAPACITY)})

def update_config_players():
    """Update config.ini P1..P6 and [Game] Players based on ROOMS.

    Rules:
    - For mic1..mic6, merge multiple names with ' & '. If empty, write 'None'.
    - Players value in [Game] is: 1 if no players, 1-4 if player count in that range,
      or 6 if 5 or more players.
    """
    try:
        base_dir = os.path.dirname(__file__)
        cfg_path = os.path.realpath(os.path.join(base_dir, args.usdx_dir, 'config.ini'))
        if not os.path.exists(cfg_path):
            logger.warning('Config path not found: %s', cfg_path)
            return False

        cp = NoSpaceConfigParser()
        # preserve case for keys
        cp.optionxform = str
        with open(cfg_path, 'r', encoding='utf-8', errors='ignore') as fh:
            cp.read_file(fh)

        # Ensure sections exist
        if 'Name' not in cp:
            cp.add_section('Name')
        if 'Game' not in cp:
            cp.add_section('Game')

        # Build P1..P6 values from ROOMS
        player_names = []
        player_delays = []
        for i in range(1, 7):
            room_key = f'mic{i}'
            users = ROOMS.get(room_key, [])
            if users:
                # merge multiple players in a single mic with ' & '
                merged = ' & '.join(users)
                player_names.append(merged)
                # Compute average delay for all users in the room
                delays = []
                for user in users:
                    # Find all session ids for this username (could be more than one if duplicate names)
                    for sid_k, uname in SESSION_USERNAMES.items():
                        if uname == user:
                            delay = SESSION_DELAYS.get(sid_k)
                            if delay is not None:
                                try:
                                    delays.append(int(delay))
                                except Exception:
                                    pass
                if delays:
                    delay_ms = int(sum(delays) / len(delays))
                else:
                    delay_ms = 0
                player_delays.append(str(delay_ms))
            else:
                player_names.append('None')
                player_delays.append('0')

        # Write P1..P6
        for i, name in enumerate(player_names, start=1):
            cp['Name'][f'P{i}'] = name

        # Write PlayerDelay P1..P6 in [PlayerDelay]
        if 'PlayerDelay' not in cp:
            cp.add_section('PlayerDelay')
        for i, d in enumerate(player_delays, start=1):
            cp['PlayerDelay'][f'P{i}'] = str(d)

        # Player count is determined by the highest mic index in use:
        # Mic 1 -> 1, Mic 2 -> 2, Mic 3 -> 3, Mic 4 -> 4, Mic 5 -> 6, Mic 6 -> 6
        highest = 0
        for idx, pname in enumerate(player_names, start=1):
            if pname != 'None':
                highest = idx

        if highest == 0:
            # no players -> default to 1
            players_value = '1'
        elif 1 <= highest <= 4:
            players_value = str(highest)
        else:
            # highest is 5 or 6 -> set to 6
            players_value = '6'

        cp['Game']['Players'] = players_value

        # Write atomically to avoid corruption
        tmp_path = cfg_path + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as fh:
            cp.write(fh)
        os.replace(tmp_path, cfg_path)
        logger.info('Updated config.ini players: P1..P6=%s Players=%s', player_names, players_value)
        return True
    except Exception as e:
        logger.exception('Failed to update config.ini players: %s', e)
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return False

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
                allowed_root = os.path.realpath(os.path.join(os.path.dirname(__file__), args.usdx_dir))
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
        upl_path = os.path.realpath(os.path.join(os.path.dirname(__file__), args.usdx_dir, 'playlists', args.playlist_name))

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
        allowed_root = os.path.realpath(os.path.join(base_dir, args.usdx_dir, 'songs'))

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

            audio_ext = args.audio_format if 'args' in globals() else 'm4a'
            m4apath = found.get(audio_ext)
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
    logger.info("Received signal %d, shutting down gracefully...", signum)
    WebRTCMicrophoneManager().stop()
    print("Terminating server...")
    sys.exit(0)

def initialize_record_section():
    """Initialize the [Record] section in config.ini for 6 virtual sinks."""
    print("Initializing [Record] section in config.ini for 6 virtual sinks...")
    base_dir = os.path.dirname(__file__)
    cfg_path = os.path.realpath(os.path.join(base_dir, args.usdx_dir, 'config.ini'))
    if not os.path.exists(cfg_path):
        logger.error(f"Config path not found: {cfg_path}")
        sys.exit(1)
    cp = NoSpaceConfigParser()
    cp.optionxform = str
    with open(cfg_path, 'r', encoding='utf-8', errors='ignore') as fh:
        cp.read_file(fh)
    # Ensure [Record] section exists
    if 'Record' not in cp:
        cp.add_section('Record')
    # Remove all DeviceName, Input, Latency, Channel1, Channel2 keys
    keys_to_remove = [k for k in cp['Record'] if any(k.startswith(prefix) for prefix in ['DeviceName', 'Input', 'Latency', 'Channel1', 'Channel2'])]
    for k in keys_to_remove:
        cp.remove_option('Record', k)
    # Add 6 virtual sinks
    for i in range(1, 6):
        cp['Record'][f'DeviceName[{i}]'] = f'smartphone-mic-{i}-sink Audio/Source/Virtual sink'
        cp['Record'][f'Input[{i}]'] = '0'
        cp['Record'][f'Latency[{i}]'] = '-1'
        cp['Record'][f'Channel1[{i}]'] = str(i)
    # Write back
    tmp_path = cfg_path + '.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as fh:
        cp.write(fh)
    os.replace(tmp_path, cfg_path)
    logger.info("[Record] section in config.ini initialized for 6 virtual sinks.")

def setup_domain_hotspot_mapping(domain):
    print(f"Setting up domain '{domain}' to map to Wi-Fi hotspot IP addresses...")
    CONF_PATH = "/etc/NetworkManager/dnsmasq-shared.d/usdx.conf"
    try:
        iw_result = subprocess.run(["iw", "dev"], capture_output=True, text=True)
        hotspots = []
        iface = None
        for line in iw_result.stdout.splitlines():
            if "Interface" in line:
                iface = line.split()[1]
            if "type AP" in line and iface:
                hotspots.append(iface)
                iface = None
        if not hotspots:
            logger.info("No Wi-Fi hotspot found.")
        for IFACE in hotspots:
            ip_result = subprocess.run(["ip", "-4", "-o", "addr", "show", "dev", IFACE], capture_output=True, text=True)
            ip = None
            for part in ip_result.stdout.split():
                if "/" in part:
                    ip = part.split("/")[0]
                    break
            if not ip:
                logger.info(f"Could not determine IP address for {IFACE}. Skipping.")
                continue
            logger.info(f"Hotspot device: {IFACE}")
            logger.info(f"IP address: {ip}")
            logger.info("----")
            # Prepare new config content
            new_content = f"address=/{domain}/{ip}\nlocal-ttl=86400\n"
            # Read current config if exists
            current_content = None
            if os.path.exists(CONF_PATH):
                try:
                    with open(CONF_PATH, "r") as conf:
                        current_content = conf.read()
                except Exception:
                    current_content = None
            # Only update if content differs
            if current_content != new_content:
                # Backup config with sudo
                if os.path.exists(CONF_PATH):
                    subprocess.run(["sudo", "cp", CONF_PATH, CONF_PATH + ".bak"])
                    logger.info(f"Backed up {CONF_PATH} to {CONF_PATH}.bak")
                # Write new config with sudo tee
                proc = subprocess.run(["echo", new_content], capture_output=True, text=True)
                tee_proc = subprocess.run(["sudo", "tee", CONF_PATH], input=proc.stdout, text=True, capture_output=True)
                if tee_proc.returncode != 0:
                    logger.error(f"Failed to write {CONF_PATH}: {tee_proc.stderr}")
                    sys.exit(1)
                # Set world-readable permissions
                subprocess.run(["sudo", "chmod", "644", CONF_PATH])
                logger.info(f"Updated {CONF_PATH}:")
                logger.info(new_content)
                # Restart NetworkManager
                logger.info("Restarting NetworkManager to apply changes...")
                result = subprocess.run(["sudo", "systemctl", "restart", "NetworkManager"])
                if result.returncode == 0:
                    logger.info("NetworkManager restarted successfully.")
                else:
                    logger.error("Failed to restart NetworkManager. Please check manually.")
            else:
                logger.info(f"No changes needed for {CONF_PATH}.")
        logger.info("All done!")
    except Exception as e:
        logger.error("Error during domain setup:", e)
        sys.exit(1)

def remap_ssl_port():
    print("Remapping port 443 to", args.port, "using iptables...")
    try:
        # Prefer to remap only the hotspot device IPs when provided.
        ip_list = []
        hotspot_dev = getattr(args, 'hotspot_device', None)
        if hotspot_dev:
            # Only consider addresses on the hotspot interface
            res = None
            while res == None or res.stdout == "":
                res = subprocess.run(["ip", "-4", "-o", "addr", "show", "dev", hotspot_dev], capture_output=True, text=True)
                time.sleep(1)
                print(f"Cannot see the IP yet...")
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 4:
                        ip_addr = parts[3].split('/')[0]
                        if ip_addr and not ip_addr.startswith('127.') and not ip_addr.startswith('0.'):
                            ip_list.append(ip_addr)
            if not ip_list:
                logger.info("No valid IPv4 addresses found on hotspot device '%s' for remapping.", hotspot_dev)
                return
        else:
            # No hotspot device specified: warn and fall back to scanning non-loopback addresses
            logger.warning("No --hotspot-device specified; remapping will consider non-loopback addresses on all interfaces (may remap more than intended).")
            res = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 4:
                        iface = parts[1]
                        ip_addr = parts[3].split('/')[0]
                        # Exclude loopback, docker, and local addresses
                        if iface.startswith('lo') or iface.startswith('docker'):
                            continue
                        if ip_addr.startswith('127.') or ip_addr.startswith('0.'):
                            continue
                        ip_list.append(ip_addr)
            if not ip_list:
                logger.info("No valid global IPv4 addresses found for remapping.")
                return

        # Apply iptables rules only for the collected IPs
        for ip_addr in ip_list:
            check_cmd = ["sudo", "iptables", "-t", "nat", "-C", "PREROUTING", "-p", "tcp", "-d", ip_addr, "--dport", "443", "-j", "REDIRECT", "--to-port", str(args.port)]
            print("cmd:", " ".join(check_cmd))
            check_result = subprocess.run(check_cmd, capture_output=True, text=True)
            if check_result.returncode == 0:
                logger.info("Rule for %s:443 -> %s already exists, skipping.", ip_addr, args.port)
                continue
            cmd = ["sudo", "iptables", "-t", "nat", "-A", "PREROUTING", "-p", "tcp", "-d", ip_addr, "--dport", "443", "-j", "REDIRECT", "--to-port", str(args.port)]
            print("cmd:", " ".join(check_cmd))
            logger.info("Running: %s", " ".join(cmd))
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                logger.info("Remapped 443 to %d for %s", args.port, ip_addr)
            else:
                logger.error("Failed to remap for %s: %s", ip_addr, result.stderr)
    except Exception as e:
        logger.error("Error remapping SSL port: %s", e)

def handle_start_hotspot(hotspot_name):
    if not hotspot_name:
        return
    waiting = 0
    while waiting < 30:
        status = subprocess.run(["nmcli", "c", "show", hotspot_name], capture_output=True, text=True)
        if status.returncode == 0:
            for line in status.stdout.splitlines():
                if line.strip().startswith("IP4.ADDRESS"):
                    ip = line.split(":", 1)[1].strip()
                    if ip and ip != '--':
                        print(f"Hotspot '{hotspot_name}' is up with IP {ip}.")
                        return
        if waiting == 0:
          logger.info(f"Bringing up hotspot '{hotspot_name}' with nmcli...")
          result = subprocess.run(["nmcli", "c", "up", hotspot_name], capture_output=True, text=True)
          waiting = 1
          if result.returncode != 0:
              logger.error(f"Failed to start hotspot '{hotspot_name}': {result.stderr}")
              sys.exit(1)
        else:
            logger.info(f"Waiting for an IP address...")
            waiting += 1
            time.sleep(1)
    logger.error(f"Timeout waiting for hotspot '{hotspot_name}' to have an IP address.")
    sys.exit(1)

def setup_iptables_forwarding(internet_device, hotspot_device):
    print(f"Setting up forwarding from internet={internet_device} to hotspot={hotspot_device}")
    rules = [
        {
            "check": ["sudo", "iptables", "-t", "nat", "-C", "POSTROUTING", "-o", hotspot_device, "-j", "MASQUERADE"],
            "add":   ["sudo", "iptables", "-t", "nat", "-A", "POSTROUTING", "-o", hotspot_device, "-j", "MASQUERADE"]
        },
        {
            "check": ["sudo", "iptables", "-C", "FORWARD", "-i", hotspot_device, "-o", internet_device, "-m", "state", "--state", "RELATED,ESTABLISHED", "-j", "ACCEPT"],
            "add":   ["sudo", "iptables", "-A", "FORWARD", "-i", hotspot_device, "-o", internet_device, "-m", "state", "--state", "RELATED,ESTABLISHED", "-j", "ACCEPT"]
        },
        {
            "check": ["sudo", "iptables", "-C", "FORWARD", "-i", internet_device, "-o", hotspot_device, "-j", "ACCEPT"],
            "add":   ["sudo", "iptables", "-A", "FORWARD", "-i", internet_device, "-o", hotspot_device, "-j", "ACCEPT"]
        }
    ]
    for rule in rules:
        logger.info("Checking: %s", " ".join(rule["check"]))
        check_result = subprocess.run(rule["check"], capture_output=True, text=True)
        if check_result.returncode == 0:
            logger.info("Rule already exists: %s", " ".join(rule['add']))
            continue
        add_result = subprocess.run(rule["add"], capture_output=True, text=True)
        if add_result.returncode == 0:
            logger.info("Added rule: %s", " ".join(rule["add"]))
        else:
            logger.error("Failed to add rule: %s\n%s", " ".join(rule["add"]), add_result.stderr)


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description="SmartMicrophone server")

    # Networking & Security
    net_group = parser.add_argument_group('Networking & Security')
    net_group.add_argument('--start-hotspot', type=str, default='', help='Start the given hotspot using nmcli before domain setup')
    net_group.add_argument('--internet-device', type=str, default='', help='Network interface providing internet connectivity (e.g., wlan0)')
    net_group.add_argument('--hotspot-device', type=str, default='', help='Network interface for the hotspot (e.g., wlan1)')
    net_group.add_argument('--ssl', action='store_true', help='Enable SSL (requires --chain and --key)')
    net_group.add_argument('--chain', type=str, default=None, help='SSL chain/cert file (fullchain.pem or cert.pem)')
    net_group.add_argument('--key', type=str, default=None, help='SSL private key file (privkey.pem)')
    net_group.add_argument('--port', type=int, default=5000, help='Port to run the server on (default: 5000)')
    net_group.add_argument('--remap-ssl-port', action='store_true', help='Remap ports so that users can access the server on the default HTTPS port. Invokes iptables and sudo!')
    net_group.add_argument('--domain', type=str, default='localhost', help='Setup a domain to hotspot IP mapping via NetworkManager/dnsmasq, requires sudo')

    # UltraStar Deluxe Integration
    usdx_group = parser.add_argument_group('UltraStar Deluxe Integration')
    usdx_group.add_argument('--usdx-dir', type=str, default='../usdx', help='Path to usdx directory (default: ../usdx)')
    usdx_group.add_argument('--playlist-name', type=str, default='SmartMicSession.upl', help='Playlist filename (default: SmartMicSession.upl)')
    usdx_group.add_argument('--run-usdx', action='store_true', help='Run UltraStar Deluxe after server startup')
    usdx_group.add_argument('--audio-format', type=str, default='m4a', help='Audio format of songs in UltraStar Deluxe (default: m4a)')
    usdx_group.add_argument('--set-inputs', action='store_true', help='Initialize [Record] section in config.ini for 6 virtual sinks')

    # Server Options
    server_group = parser.add_argument_group('Server Options')
    server_group.add_argument('--debug', action='store_true', help='Enable debug mode')
    server_group.add_argument('--skip-scan-songs', action='store_true', help='Skip scanning songs and building songs_index.json at startup')
    server_group.add_argument('--control-password', type=str, default=None, help='Require this password before accessing the Control tab')

    args = parser.parse_args()

    CONTROL_PASSWORD = args.control_password

    signal.signal(signal.SIGINT, signal_handler)

    # Truncate the logfile on startup so each run starts fresh
    try:
        logfile_path = os.path.join(os.path.dirname(__file__), 'virtual-microphone.log')
        # Open in write mode to truncate or create the file
        with open(logfile_path, 'w', encoding='utf-8'):
            pass
    except Exception:
        # If truncation fails, continue and let logging create/append as fallback
        pass

    logging.basicConfig(filename=logfile_path, level=logging.INFO if not args.debug else logging.DEBUG)

    # Run iptables forwarding if both devices are provided
    if args.internet_device and args.hotspot_device:
        setup_iptables_forwarding(args.internet_device, args.hotspot_device)

    if args.start_hotspot:
        handle_start_hotspot(args.start_hotspot)

    if args.set_inputs:
        initialize_record_section()

    if args.domain != 'localhost':
        setup_domain_hotspot_mapping(args.domain)

    WebRTCMicrophoneManager()

    # Start background thread to clean up stale sessions that haven't polled /status
    def stale_cleanup_loop():
        mgr = WebRTCMicrophoneManager()
        while True:
            try:
                now = time.time()
                stale = []
                for sid, last in list(LAST_SEEN.items()):
                    if now - last > 10.0:
                        # If this session has an associated microphone process that is still alive,
                        # treat the session as active and skip stale removal.
                        try:
                            mic = mgr.microphones.get(sid)
                            if mic:
                                try:
                                    if mic.is_process_alive():
                                        logger.debug('Session %s has active microphone; skipping stale removal', sid)
                                        continue
                                except Exception:
                                    # If mic liveness check fails, fall back to treating as stale
                                    logger.exception('Error checking mic liveness for session %s', sid)
                        except Exception:
                            logger.exception('Error inspecting microphones for session %s', sid)
                        stale.append(sid)
                for sid in stale:
                    try:
                        logger.info('Stale session detected: %s, removing associated microphone', sid)
                        mgr.remove_microphone(sid)
                        # Remove from last-seen map
                        LAST_SEEN.pop(sid, None)
                        # Remove username from rooms if present
                        try:
                            uname = SESSION_USERNAMES.pop(sid, None)
                            if uname:
                                for r, users in list(ROOMS.items()):
                                    if uname in users:
                                        ROOMS[r] = [u for u in users if u != uname]
                                try:
                                    notify_rooms_update()
                                except Exception:
                                    logger.exception('Failed to notify rooms after stale removal')
                                try:
                                    update_config_players()
                                except Exception:
                                    logger.exception('Failed to update config players after stale removal')
                            SESSION_ROOMS.pop(sid, None)
                        except Exception:
                            logger.exception('Error removing user mapping for stale session %s', sid)
                        # Release control ownership if this session held it
                        try:
                            global CONTROL_OWNER, CONTROL_OWNER_NAME, CONTROL_TIMESTAMP
                            if CONTROL_OWNER == sid:
                                CONTROL_OWNER = None
                                CONTROL_OWNER_NAME = None
                                CONTROL_TIMESTAMP = 0
                                try:
                                    notify_rooms_update()
                                except Exception:
                                    pass
                        except Exception:
                            logger.exception('Error releasing control for stale session %s', sid)
                    except Exception:
                        logger.exception('Failed to remove stale microphone for session %s', sid)
                time.sleep(2.0)
            except Exception:
                logger.exception('Exception in stale cleanup loop')

    try:
        t = threading.Thread(target=stale_cleanup_loop, daemon=True)
        t.start()
    except Exception:
        logger.exception('Failed to start stale cleanup thread')

    # Build/update song index at startup (can be skipped with --skip-scan-songs)
    if not getattr(args, 'skip_scan_songs', False):
        try:
            print("Scanning songs and building index...")
            scan_songs_and_build_index(find_root=args.usdx_dir)
        except Exception:
            logger.exception('Error scanning songs at startup')
    else:
        logger.info('Skipping songs scan at startup (--skip-scan-songs)')

    # Ensure playlist file exists and is truncated at startup
    try:
        base_dir = os.path.dirname(__file__)
        upl_dir = os.path.realpath(os.path.join(base_dir, args.usdx_dir, 'playlists'))
        os.makedirs(upl_dir, exist_ok=True)
        upl_path = os.path.join(upl_dir, args.playlist_name)
        # Truncate/create the file
        with open(upl_path, 'w', encoding='utf-8') as fh:
            fh.truncate(0)
        print(f'Initialized playlist {upl_path}')
    except Exception:
        logger.exception('Failed to create/truncate playlist file')

    # Set the port from command line argument
    port = args.port
    
    # Remap SSL port if requested
    if args.remap_ssl_port:
        remap_ssl_port()

    # Optionally run UltraStar Deluxe after server startup
    if args.run_usdx:
        print("Launching UltraStar Deluxe...")
        try:
            exe_path = os.path.abspath(os.path.join(args.usdx_dir, "ultrastardx"))
            cwd_path = os.path.abspath(args.usdx_dir)
            subprocess.Popen([exe_path], cwd=cwd_path)
            logger.info("UltraStar Deluxe launched.")
        except Exception as e:
            logger.error("Failed to launch UltraStar Deluxe: %s", e)

    # SSL context handling
    ssl_context = None
    if args.ssl:
        if args.chain and args.key:
            ssl_context = (args.chain, args.key)
    
    if ssl_context:
        logger.info("Starting SmartMicrophone server with SSL on port %d...", port)
        if port == 443 or args.remap_ssl_port:
            print(f"Access the server at: https://{args.domain}/")
        else:
            print(f"Access the server at: https://{args.domain}:{port}/")
        app.run(host='0.0.0.0', port=port, debug=args.debug, use_reloader=False, ssl_context=ssl_context)
    else:
        app.run(host='0.0.0.0', port=port, debug=args.debug, use_reloader=False)
        print(f"Access the server at: https://{args.domain}:{port}/")
