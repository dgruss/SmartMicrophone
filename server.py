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
import re



DECODER_REGEX = re.compile(r'Using decoder FFmpeg_Decoder for "(?P<path>[^"]+)"')

# Automation phase identifiers
PHASE_IDLE = 'idle'
PHASE_PRE_OPEN_COUNTDOWN = 'pre_open_countdown'
PHASE_PLAYER_SELECTION_COUNTDOWN = 'player_selection_countdown'
PHASE_AWAITING_SONG_START = 'awaiting_song_start'
PHASE_SINGING = 'singing'
PHASE_SCORES_COUNTDOWN = 'scores_countdown'
PHASE_HIGHSCORE_COUNTDOWN = 'highscore_countdown'
PHASE_AWAITING_SONG_LIST = 'awaiting_song_list'
PHASE_NEXT_SONG_COUNTDOWN = 'next_song_countdown'

PHASE_STATUS_MAP = {
    PHASE_IDLE: 'idle',
    PHASE_PRE_OPEN_COUNTDOWN: 'pre_open_countdown',
    PHASE_PLAYER_SELECTION_COUNTDOWN: 'player_selection_countdown',
    PHASE_AWAITING_SONG_START: 'awaiting_song_start',
    PHASE_SINGING: 'singing',
    PHASE_SCORES_COUNTDOWN: 'scores_countdown',
    PHASE_HIGHSCORE_COUNTDOWN: 'highscore_countdown',
    PHASE_AWAITING_SONG_LIST: 'awaiting_song_list',
    PHASE_NEXT_SONG_COUNTDOWN: 'next_song_countdown',
}

VIDEO_PLAYING_REGEX = re.compile(r'(Playing\s+video|Video\s*:|Start\s+video)', re.IGNORECASE)
STATUS_END_ONSHOW_REGEX = re.compile(r'STATUS:\s*End\s*\[OnShow\]', re.IGNORECASE)

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


def _countdown_overlay_script():
    return os.path.join(BASE_DIR, 'countdown_overlay.py')


def _launch_countdown_overlay(seconds):
    global OVERLAY_PROCESS
    script_path = _countdown_overlay_script()
    if not os.path.isfile(script_path):
        logger.warning('Countdown overlay script missing at %s; skipping overlay', script_path)
        return
    if sys.platform.startswith('linux') and not os.environ.get('DISPLAY'):
        logger.warning('DISPLAY not set; skipping countdown overlay launch')
        return
    try:
        seconds_int = max(1, int(seconds))
    except Exception:
        seconds_int = 15
    with OVERLAY_LOCK:
        if OVERLAY_PROCESS and OVERLAY_PROCESS.poll() is None:
            try:
                OVERLAY_PROCESS.terminate()
            except Exception:
                pass
        try:
            OVERLAY_PROCESS = subprocess.Popen([sys.executable, script_path, str(seconds_int)])
            logger.info('Started server-side countdown overlay for %ss', seconds_int)
        except Exception:
            OVERLAY_PROCESS = None
            logger.exception('Failed to launch countdown overlay via %s', script_path)


def _stop_countdown_overlay():
    global OVERLAY_PROCESS
    with OVERLAY_LOCK:
        if OVERLAY_PROCESS and OVERLAY_PROCESS.poll() is None:
            try:
                OVERLAY_PROCESS.terminate()
                logger.debug('Stopped server-side countdown overlay process')
            except Exception:
                logger.exception('Failed to terminate countdown overlay process')
            finally:
                OVERLAY_PROCESS = None
        else:
            OVERLAY_PROCESS = None

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

# When True, the server runs in control-only mode (no microphone/WebRTC features)
CONTROL_ONLY_MODE = False

# Maximum characters allowed for display names (can be overridden via CLI)
MAX_NAME_LENGTH = 16


PLAYLIST_FILE_LOCK = threading.Lock()
PLAYLIST_STATE_LOCK = threading.Lock()
SONGS_BY_AUDIO = {}
PLAYLIST_THREAD = None
PLAYLIST_THREAD_STOP = threading.Event()
PLAYLIST_LOG_POSITION = 0
PLAYLIST_COUNTDOWN_DEFAULT = 15
USDX_LOG_FILE = None
USDX_LOG_CANDIDATES = []
OVERLAY_PROCESS = None
OVERLAY_LOCK = threading.Lock()


def _default_playlist_state():
    return {
        'enabled': False,
        'status': 'disabled',
        'countdown_seconds': PLAYLIST_COUNTDOWN_DEFAULT,
        'countdown_deadline': None,
        'countdown_token': 0,
        'phase_token': 0,
        'automation_phase': PHASE_IDLE,
        'current_index': 0,
        'current_song': None,
        'next_song': None,
        'pending_index': None,
        'pending_song': None,
        'last_decoder_path': None,
        'auto_added': 0,
        'last_error': None,
        'last_status_change': time.time(),
        'song_started_at': None,
        'phase_timeout': None,
        'playlist_initialized': False,
        'decoder_event_count': 0,
        'decoder_last_timestamp': None,
        'decoder_last_label': None,
        'decoder_score_triggered': False,
    }


PLAYLIST_STATE = _default_playlist_state()


def _playlist_audio_key():
    if 'args' in globals():
        try:
            key = getattr(args, 'audio_format', 'm4a')
            if key:
                return key
        except Exception:
            pass
    return 'm4a'


def _normalize_audio_path(path):
    if not path:
        return None
    candidate = path
    if not os.path.isabs(candidate):
        candidate = os.path.join(BASE_DIR, candidate)
    try:
        return os.path.realpath(candidate)
    except Exception:
        return None


def _normalize_log_candidate(path):
    if not path:
        return None
    expanded = os.path.expanduser(path)
    try:
        resolved = os.path.realpath(expanded)
    except Exception:
        resolved = expanded
    return resolved


def _build_usdx_log_candidates(custom_path=None):
    candidates = []

    def _add(path):
        normalized = _normalize_log_candidate(path)
        if normalized and normalized not in candidates:
            candidates.append(normalized)

    if custom_path:
        _add(custom_path)

    usdx_dir = None
    if 'args' in globals():
        try:
            usdx_dir = getattr(args, 'usdx_dir', None)
        except Exception:
            usdx_dir = None
    if usdx_dir:
        if not os.path.isabs(usdx_dir):
            usdx_dir = os.path.realpath(os.path.join(BASE_DIR, usdx_dir))
        _add(os.path.join(usdx_dir, 'Error.log'))

    _add(os.path.join(BASE_DIR, '..', 'usdx', 'Error.log'))
    _add('~/usdx/Error.log')
    _add(os.path.join(BASE_DIR, 'Error.log'))

    return candidates


def _set_usdx_log_file(path, seek_end=True):
    global USDX_LOG_FILE, PLAYLIST_LOG_POSITION
    if not path:
        return
    USDX_LOG_FILE = path
    if not os.path.exists(path):
        PLAYLIST_LOG_POSITION = 0
        return
    try:
        with open(path, 'rb') as fh:
            fh.seek(0, os.SEEK_END if seek_end else os.SEEK_SET)
            if seek_end:
                PLAYLIST_LOG_POSITION = fh.tell()
            else:
                PLAYLIST_LOG_POSITION = 0
    except Exception:
        PLAYLIST_LOG_POSITION = 0


def _initialize_usdx_log_monitor(custom_path=None):
    global USDX_LOG_CANDIDATES
    USDX_LOG_CANDIDATES = _build_usdx_log_candidates(custom_path)
    selected = None
    for candidate in USDX_LOG_CANDIDATES:
        if os.path.exists(candidate):
            selected = candidate
            break
    if selected:
        _set_usdx_log_file(selected, seek_end=True)
        logger.info('Monitoring USDX log file: %s', selected)
    else:
        if USDX_LOG_CANDIDATES:
            logger.warning('USDX log file not found yet; will monitor once available. Candidates: %s', ', '.join(USDX_LOG_CANDIDATES))
            _set_usdx_log_file(USDX_LOG_CANDIDATES[0], seek_end=True)
        else:
            logger.warning('No USDX log file candidates could be determined')


def _ensure_usdx_log_file():
    global USDX_LOG_FILE
    if USDX_LOG_FILE and os.path.exists(USDX_LOG_FILE):
        return True
    for candidate in USDX_LOG_CANDIDATES:
        if os.path.exists(candidate):
            if candidate != USDX_LOG_FILE:
                logger.info('Switching USDX log monitor to %s', candidate)
            _set_usdx_log_file(candidate, seek_end=True)
            return True
    return False


def _register_song_entry(entry):
    if not entry:
        return
    audio_key = _playlist_audio_key()
    candidates = []
    try:
        if entry.get(audio_key):
            candidates.append(entry.get(audio_key))
    except Exception:
        pass
    for fallback in ('m4a', 'mp3', 'ogg', 'wav'):
        if fallback == audio_key:
            continue
        candidate = entry.get(fallback)
        if candidate:
            candidates.append(candidate)
    seen = set()
    for candidate in candidates:
        normalized = _normalize_audio_path(candidate)
        if normalized and normalized not in seen:
            SONGS_BY_AUDIO[normalized] = entry
            seen.add(normalized)


def playlist_file_path():
    playlist_name = 'SmartMicSession.upl'
    usdx_dir = '../usdx'
    if 'args' in globals():
        try:
            playlist_name = args.playlist_name
            usdx_dir = args.usdx_dir
        except Exception:
            pass
    base_dir = os.path.dirname(__file__)
    return os.path.realpath(os.path.join(base_dir, usdx_dir, 'playlists', playlist_name))


def _read_playlist_lines_unlocked():
    try:
        with open(playlist_file_path(), 'r', encoding='utf-8') as fh:
            return [l.strip() for l in fh if l.strip()]
    except FileNotFoundError:
        return []
    except Exception:
        logger.exception('Failed to read playlist file')
        return []


def _write_playlist_lines_unlocked(lines):
    path = playlist_file_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        for line in lines:
            if line:
                fh.write(line + '\n')


def get_playlist_lines():
    with PLAYLIST_FILE_LOCK:
        return list(_read_playlist_lines_unlocked())


def write_playlist_lines(lines):
    with PLAYLIST_FILE_LOCK:
        _write_playlist_lines_unlocked(lines)


def normalize_playlist_label(raw):
    if not raw:
        return None
    label = str(raw).strip()
    if not label:
        return None
    if ' : ' in label:
        artist, title = label.split(':', 1)
        return f"{artist.strip()} : {title.strip()}"
    if ' - ' in label:
        artist, title = label.split('-', 1)
        artist = artist.strip()
        title = title.strip()
        if artist and title:
            return f"{artist} : {title}"
    return label


def _parse_artist_title_from_txt(entry):
    txt_rel = entry.get('txt') if entry else None
    if not txt_rel:
        return None
    try:
        candidate_txt = txt_rel
        if not os.path.isabs(candidate_txt):
            candidate_txt = os.path.realpath(os.path.join(BASE_DIR, candidate_txt))
        allowed_root = os.path.realpath(os.path.join(BASE_DIR, args.usdx_dir)) if 'args' in globals() else None
        if allowed_root and not candidate_txt.startswith(allowed_root):
            return None
        if not os.path.exists(candidate_txt):
            return None
        artist = None
        title = None
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
        if artist and title:
            return f"{artist} : {title}"
        if artist:
            return artist
        if title:
            return title
    except Exception:
        logger.exception('Failed to parse artist/title from txt: %s', txt_rel)
    return None


def derive_playlist_label(entry):
    if not entry:
        return None
    cached = entry.get('_playlist_label')
    if cached:
        return cached
    label = _parse_artist_title_from_txt(entry)
    if not label:
        display = entry.get('display')
        if display:
            label = display
        else:
            txt_path = entry.get('txt')
            if txt_path:
                label = os.path.splitext(os.path.basename(txt_path))[0].replace('_', ' ')
    label = normalize_playlist_label(label or '')
    if label:
        entry['_playlist_label'] = label
    return label


def _current_countdown_duration(custom_seconds=None):
    if custom_seconds is not None:
        try:
            return max(1, int(custom_seconds))
        except Exception:
            pass
    with PLAYLIST_STATE_LOCK:
        base = PLAYLIST_STATE.get('countdown_seconds', PLAYLIST_COUNTDOWN_DEFAULT)
    try:
        return max(1, int(base))
    except Exception:
        return PLAYLIST_COUNTDOWN_DEFAULT


def _activate_countdown_phase(phase, duration=None, overlay=True, timeout=None):
    duration_val = _current_countdown_duration(duration)
    now = time.time()
    with PLAYLIST_STATE_LOCK:
        state = PLAYLIST_STATE
        state['automation_phase'] = phase
        state['status'] = PHASE_STATUS_MAP.get(phase, phase)
        state['countdown_deadline'] = now + duration_val
        state['countdown_token'] += 1
        state['phase_token'] = state['countdown_token']
        state['phase_timeout'] = now + timeout if timeout else None
        state['last_status_change'] = now
        token = state['countdown_token']
    if overlay:
        _launch_countdown_overlay(duration_val)
    return token, duration_val


def _activate_phase(phase, timeout=None):
    now = time.time()
    with PLAYLIST_STATE_LOCK:
        state = PLAYLIST_STATE
        state['automation_phase'] = phase
        state['status'] = PHASE_STATUS_MAP.get(phase, phase)
        state['countdown_deadline'] = None
        state['phase_token'] = state.get('countdown_token', 0)
        state['phase_timeout'] = now + timeout if timeout else None
        state['last_status_change'] = now
        if phase == PHASE_AWAITING_SONG_START:
            state['decoder_event_count'] = 0
            state['decoder_last_timestamp'] = None
            state['decoder_last_label'] = None
            state['decoder_score_triggered'] = False


def _set_playlist_error(message):
    with PLAYLIST_STATE_LOCK:
        state = PLAYLIST_STATE
        state['status'] = 'error'
        state['automation_phase'] = PHASE_IDLE
        state['countdown_deadline'] = None
        state['phase_timeout'] = None
        state['decoder_event_count'] = 0
        state['decoder_last_timestamp'] = None
        state['decoder_last_label'] = None
        state['decoder_score_triggered'] = False
        state['last_error'] = message
        state['last_status_change'] = time.time()
    logger.error('Playlist automation error: %s', message)


def _append_random_song_locked(lines):
    pool = SONGS_LIST or load_songs_index()
    if not pool:
        return None
    attempts = min(64, len(pool))
    seen = set()
    while attempts > 0:
        entry = random.choice(pool)
        if not entry or entry.get('id') in seen:
            attempts -= 1
            continue
        seen.add(entry.get('id'))
        label = derive_playlist_label(entry)
        if not label or label in lines:
            attempts -= 1
            continue
        lines.append(label)
        _write_playlist_lines_unlocked(lines)
        entry['upl'] = True
        try:
            SONGS_BY_ID[str(entry.get('id'))] = entry
        except Exception:
            pass
        _register_song_entry(entry)
        return label
        attempts -= 1
    return None


def append_random_song_to_playlist():
    with PLAYLIST_FILE_LOCK:
        lines = _read_playlist_lines_unlocked()
        added = _append_random_song_locked(lines)
        return added


def ensure_playlist_has_entries(min_entries=1):
    """Ensure playlist file has at least `min_entries` entries; return (lines, added_labels)."""
    try:
        min_required = max(1, int(min_entries))
    except Exception:
        min_required = 1

    added_labels = []
    with PLAYLIST_FILE_LOCK:
        lines = _read_playlist_lines_unlocked()
        while len(lines) < min_required:
            added = _append_random_song_locked(lines)
            if not added:
                break
            added_labels.append(added)
            lines = _read_playlist_lines_unlocked()
        result_lines = list(lines)
    return result_lines, added_labels


def refresh_playlist_state_cache(lines=None):
    if lines is None:
        lines = get_playlist_lines()
    with PLAYLIST_STATE_LOCK:
        idx = PLAYLIST_STATE.get('current_index', 0)
        PLAYLIST_STATE['next_song'] = lines[idx] if idx < len(lines) else None
        return PLAYLIST_STATE['next_song']


def _prepare_pending_playlist_entry():
    auto_added = False
    appended_next_label = None
    with PLAYLIST_STATE_LOCK:
        target_index = max(0, int(PLAYLIST_STATE.get('current_index', 0) or 0))
    with PLAYLIST_FILE_LOCK:
        lines = _read_playlist_lines_unlocked()
        if target_index >= len(lines):
            added_label = _append_random_song_locked(lines)
            if added_label:
                auto_added = True
                lines = _read_playlist_lines_unlocked()
        if not lines:
            return False, 'Playlist is empty'
        if target_index >= len(lines):
            target_index = len(lines) - 1
        line_to_start = lines[target_index]
        if target_index + 1 >= len(lines):
            appended_next_label = _append_random_song_locked(lines)
            if appended_next_label:
                lines = _read_playlist_lines_unlocked()
    with PLAYLIST_STATE_LOCK:
        state = PLAYLIST_STATE
        state['pending_index'] = target_index
        state['pending_song'] = line_to_start
        state['next_song'] = line_to_start
        state['phase_timeout'] = None
        if auto_added:
            state['auto_added'] = state.get('auto_added', 0) + 1
        elif appended_next_label:
            state['auto_added'] = state.get('auto_added', 0) + 1
    logger.info('Prepared pending playlist entry index=%s label=%s (auto_added=%s appended=%s)', target_index, line_to_start, auto_added, bool(appended_next_label))
    return True, {'lines': lines, 'target_index': target_index, 'label': line_to_start}


def _handle_song_started(label=None, index=None, lines=None):
    with PLAYLIST_STATE_LOCK:
        state = PLAYLIST_STATE
        if state.get('automation_phase') != PHASE_AWAITING_SONG_START:
            return
        if label is None:
            label = state.get('pending_song') or state.get('current_song')
        if index is None:
            index = state.get('pending_index')
        if lines is None:
            lines = get_playlist_lines()
        state['automation_phase'] = PHASE_SINGING
        state['status'] = PHASE_STATUS_MAP.get(PHASE_SINGING, 'singing')
        state['countdown_deadline'] = None
        state['phase_timeout'] = None
        state['song_started_at'] = time.time()
        state['decoder_event_count'] = 0
        state['decoder_last_timestamp'] = None
        state['decoder_last_label'] = label
        state['decoder_score_triggered'] = False
        state['current_song'] = label
        if index is None:
            index = state.get('current_index', 0)
        state['current_index'] = int(index) + 1 if index is not None else state.get('current_index', 0)
        next_idx = state['current_index']
        if next_idx is not None and next_idx < len(lines):
            state['next_song'] = lines[next_idx]
        else:
            state['next_song'] = None
        state['pending_song'] = None
        state['pending_index'] = None
        state['last_error'] = None
        state['last_status_change'] = time.time()
    logger.info('Song playback detected; automation phase set to SINGING for "%s"', label or 'unknown')


def _find_playlist_index_for_label(label, lines, start_at=0):
    if not label or not lines:
        return None
    start_idx = max(0, int(start_at or 0))
    for idx in range(start_idx, len(lines)):
        if lines[idx] == label:
            return idx
    for idx in range(0, start_idx):
        if lines[idx] == label:
            return idx
    return None


def _process_decoder_path(audio_path):
    normalized = _normalize_audio_path(audio_path)
    if not normalized:
        return
    entry = SONGS_BY_AUDIO.get(normalized)
    if not entry:
        load_songs_index()
        entry = SONGS_BY_AUDIO.get(normalized)
    label = derive_playlist_label(entry) if entry else None
    lines = get_playlist_lines()
    with PLAYLIST_STATE_LOCK:
        start_hint = max(0, PLAYLIST_STATE.get('current_index', 0) - 3)
        PLAYLIST_STATE['last_decoder_path'] = normalized
        automation_phase = PLAYLIST_STATE.get('automation_phase')
        current_song = PLAYLIST_STATE.get('current_song')
        pending_song = PLAYLIST_STATE.get('pending_song')
        pending_index = PLAYLIST_STATE.get('pending_index')
    active_label = label or (pending_song if automation_phase == PHASE_AWAITING_SONG_START else current_song)
    idx = _find_playlist_index_for_label(active_label, lines, start_hint) if active_label else None
    if idx is None:
        idx = pending_index if automation_phase == PHASE_AWAITING_SONG_START else start_hint
    now = time.time()
    if automation_phase == PHASE_AWAITING_SONG_START:
        _handle_song_started(active_label, idx, lines)
        with PLAYLIST_STATE_LOCK:
            state = PLAYLIST_STATE
            state['decoder_event_count'] = 1
            state['decoder_last_timestamp'] = now
            state['decoder_last_label'] = active_label
        logger.info('Song started: %s (decoder=%s)', active_label or 'unknown', normalized)
        return

    if automation_phase == PHASE_SINGING:
        should_update_label = False
        score_count = None
        score_delta = None
        score_should_trigger = False
        with PLAYLIST_STATE_LOCK:
            state = PLAYLIST_STATE
            prev_song = state.get('current_song')
            prev_ts = state.get('decoder_last_timestamp')
            prev_count = state.get('decoder_event_count', 0) or 0
            score_already_triggered = state.get('decoder_score_triggered', False)
            label_matches_current = active_label == prev_song if active_label else True
            if not label_matches_current and active_label:
                state['current_song'] = active_label
                if state.get('current_index', 0) < len(lines):
                    state['next_song'] = lines[state['current_index']]
                state['decoder_event_count'] = 1
                state['decoder_last_timestamp'] = now
                state['decoder_last_label'] = active_label
                state['last_status_change'] = time.time()
                should_update_label = True
            else:
                score_count = prev_count + 1
                state['decoder_event_count'] = score_count
                state['decoder_last_timestamp'] = now
                state['decoder_last_label'] = active_label or prev_song
                if prev_ts:
                    score_delta = now - prev_ts
                if not score_already_triggered and (score_count >= 3 or (score_delta is not None and score_delta >= 5.0)):
                    score_should_trigger = True
        if should_update_label:
            logger.info('Updated current song to %s based on decoder log', label)
            return
        if score_should_trigger and score_count:
            if _trigger_scores_countdown():
                if score_delta is not None:
                    logger.info('Detected decoder replay after song completion; starting score confirmation countdown (count=%d, Δt=%.2fs)', score_count, score_delta)
                else:
                    logger.info('Detected decoder replay after song completion; starting score confirmation countdown (count=%d)', score_count)
            else:
                logger.debug('Decoder replay detected but scores countdown already active (count=%d)', score_count)
        return


def playlist_status_payload(lines=None):
    if lines is None:
        lines = get_playlist_lines()
    with PLAYLIST_STATE_LOCK:
        state = dict(PLAYLIST_STATE)
    now = time.time()
    countdown_remaining = 0
    if state.get('countdown_deadline'):
        countdown_remaining = max(0, int(state['countdown_deadline'] - now))
    countdown_active = state.get('countdown_deadline') is not None and countdown_remaining > 0
    status_text = {
        'disabled': 'Playlist mode disabled',
        'idle': 'Idle — ready for next song',
        'countdown': 'Countdown in progress',
        'pre_open_countdown': 'Preparing playlist…',
        'next_song_countdown': 'Next song countdown',
        'player_selection_countdown': 'Player selection countdown',
        'arming': 'Arming next song',
        'awaiting_decoder': 'Starting song…',
        'awaiting_song_start': 'Waiting for song to start…',
        'singing': 'Song in progress',
        'scores_countdown': 'Review scores in…',
        'awaiting_scores_confirmation': 'Waiting to confirm scores…',
        'highscore_countdown': 'Highscore countdown',
        'awaiting_song_list': 'Waiting for song list…',
        'error': 'Error'
    }.get(state.get('status'), state.get('status', 'idle'))
    return {
        'enabled': state.get('enabled', False),
        'status': state.get('status'),
        'automation_phase': state.get('automation_phase'),
        'status_text': status_text,
        'current_index': state.get('current_index', 0),
        'current_song': state.get('current_song'),
        'next_song': state.get('next_song'),
        'playlist_length': len(lines),
        'countdown_seconds': state.get('countdown_seconds', PLAYLIST_COUNTDOWN_DEFAULT),
        'countdown_remaining': countdown_remaining,
        'countdown_active': countdown_active,
        'last_decoder_path': state.get('last_decoder_path'),
        'auto_added': state.get('auto_added', 0),
        'lock_controls': state.get('enabled', False),
        'last_error': state.get('last_error')
    }


def set_playlist_enabled(enabled, countdown_seconds=None):
    logger.info(f'set_playlist_enabled: enabled={enabled}, countdown_seconds={countdown_seconds}')
    if enabled:
        min_required = 2
        lines, added_labels = ensure_playlist_has_entries(min_required)
        auto_seed_count = len(added_labels)
        if auto_seed_count:
            logger.info('Playlist auto-seeded with %s before enabling mode', ', '.join(added_labels))
    else:
        lines = get_playlist_lines()
        auto_seed_count = 0
    if enabled and len(lines) < 2:
        raise RuntimeError('Playlist is empty and no songs could be auto-added')
    with PLAYLIST_STATE_LOCK:
        state = PLAYLIST_STATE
        state['enabled'] = bool(enabled)
        if countdown_seconds is not None:
            try:
                state['countdown_seconds'] = max(1, int(countdown_seconds))
            except Exception:
                state['countdown_seconds'] = PLAYLIST_COUNTDOWN_DEFAULT
        state['countdown_deadline'] = None
        state['countdown_token'] += 1
        state['phase_token'] = state['countdown_token']
        state['last_error'] = None
        state['automation_phase'] = PHASE_IDLE
        state['phase_timeout'] = None
        state['playlist_initialized'] = False
        state['pending_song'] = None
        state['pending_index'] = None
        if not state['enabled']:
            state['auto_added'] = 0
        state['decoder_event_count'] = 0
        state['decoder_last_timestamp'] = None
        state['decoder_last_label'] = None
        state['decoder_score_triggered'] = False
        if state['enabled']:
            state['status'] = 'idle'
            state['current_index'] = 0
            state['current_song'] = None
            state['next_song'] = lines[0] if lines else None
            state['auto_added'] = auto_seed_count
        else:
            state['status'] = 'disabled'
            state['current_song'] = None
            state['next_song'] = None
    if not enabled:
        _stop_countdown_overlay()
    return playlist_status_payload(lines)


def request_playlist_countdown(custom_seconds=None):
    duration = _current_countdown_duration(custom_seconds)
    with PLAYLIST_STATE_LOCK:
        state = PLAYLIST_STATE
        if not state.get('enabled'):
            return False, 'Playlist mode is not enabled'
        phase = state.get('automation_phase', PHASE_IDLE)
        if phase not in (PHASE_IDLE, PHASE_AWAITING_SONG_LIST):
            return False, 'Playlist automation is busy'
        state['countdown_seconds'] = duration
    # Ensure playlist has entries before starting automation
    try:
        ensure_playlist_has_entries(2)
    except Exception as exc:
        logger.exception('Failed to ensure playlist entries: %s', exc)
        return False, str(exc)

    if not _is_playlist_initialized():
        ok, error = _begin_initial_playlist_sequence(duration)
    else:
        ok, error = _select_next_song_with_countdown(duration)

    if not ok:
        return False, error

    with PLAYLIST_STATE_LOCK:
        token = PLAYLIST_STATE.get('countdown_token')
    logger.info('Playlist countdown started for %ss (token=%s)', duration, token)
    return True, token


def trigger_playlist_sequence_immediately(custom_seconds=None):
    """Start automation immediately by opening playlist and beginning countdown."""
    logger.info(f'trigger_playlist_sequence_immediately: custom_seconds={custom_seconds}')
    duration = _current_countdown_duration(custom_seconds)
    with PLAYLIST_STATE_LOCK:
        PLAYLIST_STATE['countdown_seconds'] = duration
    ok, error = _begin_initial_playlist_sequence(duration)
    if not ok:
        return False, error
    return True, None


def _is_playlist_initialized():
    with PLAYLIST_STATE_LOCK:
        return bool(PLAYLIST_STATE.get('playlist_initialized'))


def _begin_initial_playlist_sequence(duration):
    ok, info = _prepare_pending_playlist_entry()
    if not ok:
        return False, info
    ok, error = _send_playlist_open_sequence()
    if not ok:
        return False, error
    with PLAYLIST_STATE_LOCK:
        state = PLAYLIST_STATE
        state['playlist_initialized'] = True
        state['automation_phase'] = PHASE_NEXT_SONG_COUNTDOWN
    _activate_countdown_phase(PHASE_NEXT_SONG_COUNTDOWN, duration)
    logger.info('Initial playlist sequence started; countdown to confirm song initiated')
    return True, None


def _select_next_song_with_countdown(duration):
    ok, error = _send_playlist_select_next_song_sequence()
    if not ok:
        return False, error
    _activate_countdown_phase(PHASE_NEXT_SONG_COUNTDOWN, duration)
    logger.info('Advanced to next playlist entry; countdown before confirming song initiated')
    return True, None



def _run_playlist_command_sequence(commands):
    for cmd in commands:
        if cmd[0] == 'delay':
            try:
                delay_sec = max(0.05, float(cmd[1]))
            except Exception:
                delay_sec = 0.1
            # Show countdown overlay for delays >= 2s (player selection phase)
            if delay_sec >= 2:
                try:
                    _launch_countdown_overlay(int(delay_sec))
                except Exception as e:
                    logger.warning(f'Failed to launch overlay during playlist delay: {e}')
            time.sleep(delay_sec)
            continue
        ok, out = run_xdotool_command(cmd)
        if not ok:
            return False, out or 'xdotool failure'
        time.sleep(0.05)
    return True, None


def _send_playlist_open_sequence():
    commands = [
        ['key', 'Escape'],
        ['key', 'Escape'],
        ['key', 'Escape'],
        ['key', 'Escape'],
        ['key', 'Escape'],
        ['key', 'Escape'],
        ['key', 'Escape'],
        ['key', 'Escape'],
        ['key', 'Escape'],
        ['key', 'Escape'],
        ['key', 'Return'],        # confirm default singer
        ['key', 'p'],             # open playlist mode selector
        ['key', 'Return'],        # start playlist mode / queue next entry
        ['key', 'p'],             # retry open playlist mode selector
        ['key', 'Return'],        # retry start playlist mode / queue next entry
        ['key', 'Down'],          # move to select playlist entry
        ['key', 'Down'],          # move to select playlist entry
        ['key', 'Return'],        # confirm selection
    ]
    return _run_playlist_command_sequence(commands)

def _send_playlist_confirm_song_sequence():
    commands = [
        ['key', 'Return']
    ]
    return _run_playlist_command_sequence(commands)

def _send_playlist_confirm_players_sequence():
    commands = [
        ['key', 'Return']
    ]
    return _run_playlist_command_sequence(commands)

def _send_playlist_confirm_scores_sequence():
    commands = [
        ['key', 'Return']
    ]
    return _run_playlist_command_sequence(commands)

def _send_playlist_confirm_highscore_sequence():
    commands = [
        ['key', 'Return']
    ]
    return _run_playlist_command_sequence(commands)

def _send_playlist_select_next_song_sequence():
    commands = [
        ['key', 'Down']
    ]
    return _run_playlist_command_sequence(commands)


def _on_next_song_countdown_expired(expected_token):
    with PLAYLIST_STATE_LOCK:
        current_token = PLAYLIST_STATE.get('countdown_token')
        if current_token != expected_token:
            logger.debug('Ignoring stale next-song countdown token %s (current %s)', expected_token, current_token)
            return
        PLAYLIST_STATE['countdown_deadline'] = None
    ok, error = _send_playlist_confirm_song_sequence()
    if not ok:
        _set_playlist_error(error or 'Failed to confirm song selection')
        return
    _activate_countdown_phase(PHASE_PLAYER_SELECTION_COUNTDOWN)
    logger.info('Song confirmed; waiting on player selection countdown')


def _on_player_selection_countdown_expired(expected_token):
    with PLAYLIST_STATE_LOCK:
        current_token = PLAYLIST_STATE.get('countdown_token')
        if current_token != expected_token:
            logger.debug('Ignoring stale player-selection countdown token %s (current %s)', expected_token, current_token)
            return
        PLAYLIST_STATE['countdown_deadline'] = None
    ok, error = _send_playlist_confirm_players_sequence()
    if not ok:
        _set_playlist_error(error or 'Failed to confirm players')
        return
    _activate_phase(PHASE_AWAITING_SONG_START, timeout=120)
    logger.info('Players confirmed; awaiting song start detection')


def _on_scores_countdown_expired(expected_token):
    with PLAYLIST_STATE_LOCK:
        current_token = PLAYLIST_STATE.get('countdown_token')
        if current_token != expected_token:
            logger.debug('Ignoring stale scores countdown token %s (current %s)', expected_token, current_token)
            return
        PLAYLIST_STATE['countdown_deadline'] = None
    ok, info = _prepare_pending_playlist_entry()
    if not ok:
        _set_playlist_error(info or 'Failed to prepare next playlist entry')
        return
    ok, error = _send_playlist_confirm_scores_sequence()
    if not ok:
        _set_playlist_error(error or 'Failed to confirm scores')
        return
    duration = _current_countdown_duration()
    _activate_countdown_phase(PHASE_HIGHSCORE_COUNTDOWN, duration)
    logger.info('Scores confirmed; starting highscore confirmation countdown')


def _on_highscore_countdown_expired(expected_token):
    with PLAYLIST_STATE_LOCK:
        current_token = PLAYLIST_STATE.get('countdown_token')
        if current_token != expected_token:
            logger.debug('Ignoring stale highscore countdown token %s (current %s)', expected_token, current_token)
            return
        PLAYLIST_STATE['countdown_deadline'] = None
    ok, error = _send_playlist_confirm_highscore_sequence()
    if not ok:
        _set_playlist_error(error or 'Failed to confirm highscore screen')
        return
    duration = _current_countdown_duration()
    ok, error = _select_next_song_with_countdown(duration)
    if not ok and error:
        _set_playlist_error(error or 'Failed to queue next song')
        return
    logger.info('Highscore confirmed; queued next song selection countdown')


def _handle_phase_timeout(phase):
    logger.warning('Playlist automation phase "%s" timed out; entering error state', phase)
    _set_playlist_error(f'Automation timeout while waiting for {phase}')


def _process_playlist_countdown():
    now = time.time()
    with PLAYLIST_STATE_LOCK:
        state = PLAYLIST_STATE
        if not state.get('enabled'):
            return
        deadline = state.get('countdown_deadline')
        token = state.get('countdown_token')
        phase = state.get('automation_phase', PHASE_IDLE)
        phase_timeout = state.get('phase_timeout')

    if deadline and now >= deadline:
        if phase == PHASE_NEXT_SONG_COUNTDOWN:
            _on_next_song_countdown_expired(token)
        elif phase == PHASE_PLAYER_SELECTION_COUNTDOWN:
            _on_player_selection_countdown_expired(token)
        elif phase == PHASE_SCORES_COUNTDOWN:
            _on_scores_countdown_expired(token)
        elif phase == PHASE_HIGHSCORE_COUNTDOWN:
            _on_highscore_countdown_expired(token)
        else:
            with PLAYLIST_STATE_LOCK:
                PLAYLIST_STATE['countdown_deadline'] = None
        return

    if (not deadline) and phase_timeout and now >= phase_timeout:
        _handle_phase_timeout(phase)


def _trigger_scores_countdown():
    with PLAYLIST_STATE_LOCK:
        state = PLAYLIST_STATE
        if state.get('automation_phase') != PHASE_SINGING:
            return False
        if state.get('decoder_score_triggered'):
            return False
        duration = state.get('countdown_seconds', PLAYLIST_COUNTDOWN_DEFAULT)
        state['decoder_score_triggered'] = True
        state['phase_timeout'] = None
    _activate_countdown_phase(PHASE_SCORES_COUNTDOWN, duration)
    return True


def _handle_video_playing_detected():
    if _trigger_scores_countdown():
        logger.info('Detected post-song video playback; starting score confirmation countdown')


def _process_usdx_log_lines():
    global PLAYLIST_LOG_POSITION, USDX_LOG_FILE
    if not _ensure_usdx_log_file():
        logger.debug('USDX log file not yet available; skipping log processing')
        return
    try:
        with open(USDX_LOG_FILE, 'r', encoding='utf-8', errors='ignore') as fh:
            file_size = fh.seek(0, os.SEEK_END)
            if PLAYLIST_LOG_POSITION > file_size:
                PLAYLIST_LOG_POSITION = 0
            fh.seek(PLAYLIST_LOG_POSITION)
            new_lines = fh.readlines()
            PLAYLIST_LOG_POSITION = fh.tell()
    except FileNotFoundError:
        logger.debug('USDX log file %s disappeared; will retry', USDX_LOG_FILE)
        USDX_LOG_FILE = None
        PLAYLIST_LOG_POSITION = 0
        return
    except Exception:
        logger.exception('Failed to read USDX log file %s', USDX_LOG_FILE)
        return
    if new_lines:
        logger.debug('Read %d new lines from USDX log %s', len(new_lines), USDX_LOG_FILE)
    for line in new_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if STATUS_END_ONSHOW_REGEX.search(stripped):
            logger.debug('Detected STATUS End [OnShow] log line: %s', stripped)
            _handle_song_started()
            continue
        match = DECODER_REGEX.search(stripped)
        if match:
            logger.info('Detected decoder log entry: %s', match.group('path'))
            _process_decoder_path(match.group('path'))
            continue
        if VIDEO_PLAYING_REGEX.search(stripped):
            logger.debug('Detected video playback log line: %s', stripped)
            _handle_video_playing_detected()


def playlist_automation_loop():
    while not PLAYLIST_THREAD_STOP.is_set():
        try:
            _process_playlist_countdown()
            _process_usdx_log_lines()
        except Exception:
            logger.exception('Playlist automation loop error')
        time.sleep(0.25)


def start_playlist_thread():
    global PLAYLIST_THREAD, PLAYLIST_LOG_POSITION
    if PLAYLIST_THREAD and PLAYLIST_THREAD.is_alive():
        return
    _ensure_usdx_log_file()
    PLAYLIST_THREAD = threading.Thread(target=playlist_automation_loop, daemon=True)
    PLAYLIST_THREAD.start()

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
            'audio_enabled': not CONTROL_ONLY_MODE,
            'control_only': CONTROL_ONLY_MODE,
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

    if CONTROL_ONLY_MODE:
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
        if not CONTROL_ONLY_MODE:
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
        if CONTROL_ONLY_MODE:
            return jsonify({'success': False, 'error': 'Server is running in control-only mode', 'error_code': 'control_only'}), 403
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
    return dict(
        automatically_reconnect=automatically_reconnect,
        control_only_mode=CONTROL_ONLY_MODE,
        max_name_length=MAX_NAME_LENGTH
    )


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


def require_control_lock():
    sid = session.get('session_id')
    if not sid or CONTROL_OWNER != sid:
        return jsonify({'success': False, 'error': 'Control lock required', 'error_code': 'control_required'}), 403
    guard = enforce_control_password()
    if guard is not None:
        return guard
    return None


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
        entry = {'id': i+1, 'txt': txtpath, audio_ext: audio_path, 'display': display, 'upl': False}
        entries.append(entry)
        try:
            _register_song_entry(entry)
        except Exception:
            logger.exception('Failed to register song entry for audio map: %s', entry.get('id'))

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
            try:
                global SONGS_BY_AUDIO
                SONGS_BY_AUDIO = {}
                for entry in items:
                    _register_song_entry(entry)
            except Exception:
                logger.exception('Failed to rebuild song audio map')
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
        if isinstance(username, str):
            username = username[:MAX_NAME_LENGTH]
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

        # Determine sink index for this room and connect if audio is enabled
        sink_index = 0
        if room.startswith('mic'):
            try:
                sink_index = int(room[3:])
            except Exception:
                sink_index = 0

        if not CONTROL_ONLY_MODE:
            # Connect the player's source to the correct sink if their microphone is running
            mgr = WebRTCMicrophoneManager()
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

        line = derive_playlist_label(entry)
        if not line:
            fallback = entry.get('display') or os.path.splitext(os.path.basename(entry.get('txt', '')))[0].replace('_', ' ')
            line = normalize_playlist_label(fallback or '')
        if not line:
            return jsonify({'success': False, 'error': 'Unable to derive playlist label'}), 500

        updated_lines = []
        with PLAYLIST_FILE_LOCK:
            lines = _read_playlist_lines_unlocked()
            os.makedirs(os.path.dirname(playlist_file_path()), exist_ok=True)
            if action == 'add':
                if line not in lines:
                    lines.append(line)
                    _write_playlist_lines_unlocked(lines)
                entry['upl'] = True
                updated_lines = list(lines)
            elif action == 'remove':
                newlines = [l for l in lines if l.strip() != line]
                if len(newlines) != len(lines):
                    _write_playlist_lines_unlocked(newlines)
                entry['upl'] = False
                updated_lines = list(newlines)
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
        try:
            _register_song_entry(entry)
        except Exception:
            logger.exception('Failed to refresh song audio mapping for entry %s', entry.get('id'))

        refresh_playlist_state_cache(updated_lines)

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


@app.route('/playlist/status', methods=['GET'])
def playlist_status():
    try:
        payload = playlist_status_payload()
        return jsonify({'success': True, **payload})
    except Exception as exc:
        logger.exception('Failed to fetch playlist status: %s', exc)
        return jsonify({'success': False, 'error': str(exc)}), 500


@app.route('/playlist/toggle', methods=['POST'])
def playlist_toggle():
    data = request.get_json(force=True, silent=True) or {}
    enabled = bool(data.get('enabled'))
    countdown = data.get('countdown_seconds')
    logger.info(f'playlist_toggle called: enabled={enabled}, countdown={countdown}, data={data}')

    if enabled:
        guard = require_control_lock()
        if guard is not None:
            logger.info('playlist_toggle: control lock required and not held')
            return guard
    else:
        guard = enforce_control_password()
        if guard is not None:
            logger.info('playlist_toggle: control password required and not valid')
            return guard
        sid = session.get('session_id')
        if CONTROL_OWNER and CONTROL_OWNER != sid:
            logger.warning('Playlist disable requested by session %s without control lock (owner=%s)', sid, CONTROL_OWNER)

    try:
        logger.info(f'Calling set_playlist_enabled(enabled={enabled}, countdown={countdown})')
        state = set_playlist_enabled(enabled, countdown)
        logger.info(f'set_playlist_enabled returned: {state}')
        if enabled:
            logger.info('Attempting to trigger playlist sequence immediately...')
            ok, err = trigger_playlist_sequence_immediately(countdown)
            logger.info(f'trigger_playlist_sequence_immediately returned: ok={ok}, err={err}')
            if not ok:
                logger.warning('Failed to trigger playlist sequence after enabling: %s', err)
            state = playlist_status_payload()
        return jsonify({'success': True, 'state': state})
    except Exception as exc:
        logger.exception('Failed to toggle playlist mode: %s', exc)
        return jsonify({'success': False, 'error': str(exc)}), 500


@app.route('/playlist/next', methods=['POST'])
def playlist_next():
    guard = require_control_lock()
    if guard is not None:
        return guard
    data = request.get_json(force=True, silent=True) or {}
    custom_seconds = data.get('countdown_seconds')
    ok, token_or_error = request_playlist_countdown(custom_seconds)
    if not ok:
        return jsonify({'success': False, 'error': token_or_error}), 400
    return jsonify({'success': True, 'countdown_token': token_or_error, 'state': playlist_status_payload()})


def signal_handler(signum, frame):
    logger.info("Received signal %d, shutting down gracefully...", signum)
    if not CONTROL_ONLY_MODE:
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
    net_group.add_argument('--enable-forwarding', action='store_true', help='Add iptables forwarding/MASQUERADE rules between the internet and hotspot interfaces (requires sudo)')
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
    usdx_group.add_argument('--usdx-log-file', type=str, default=None, help='Path to the UltraStar Deluxe log file for playlist resync automation')
    usdx_group.add_argument('--countdown', type=int, default=15, help='Default countdown seconds before every step in playlist mode (default: 15)')

    # Server Options
    server_group = parser.add_argument_group('Server Options')
    server_group.add_argument('--debug', action='store_true', help='Enable debug mode')
    server_group.add_argument('--skip-scan-songs', action='store_true', help='Skip scanning songs and building songs_index.json at startup')
    server_group.add_argument('--control-password', type=str, default=None, help='Require this password before accessing the Control tab')
    server_group.add_argument('--control-only', action='store_true', help='Disable microphone/WebRTC features and expose control-only web UI')
    server_group.add_argument('--max-name-length', type=int, default=16, help='Maximum characters allowed for player display names (default: 16)')

    args = parser.parse_args()

    CONTROL_PASSWORD = args.control_password
    CONTROL_ONLY_MODE = bool(args.control_only)
    try:
        MAX_NAME_LENGTH = max(1, int(args.max_name_length))
    except Exception:
        MAX_NAME_LENGTH = 16

    try:
        PLAYLIST_COUNTDOWN_DEFAULT = max(1, int(getattr(args, 'countdown', PLAYLIST_COUNTDOWN_DEFAULT)))
    except Exception:
        PLAYLIST_COUNTDOWN_DEFAULT = 15
    with PLAYLIST_STATE_LOCK:
        PLAYLIST_STATE['countdown_seconds'] = PLAYLIST_COUNTDOWN_DEFAULT

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

    _initialize_usdx_log_monitor(args.usdx_log_file)

    # Run iptables forwarding only when explicitly requested
    if args.enable_forwarding:
        if args.internet_device and args.hotspot_device:
            setup_iptables_forwarding(args.internet_device, args.hotspot_device)
        else:
            logger.warning('Cannot enable forwarding without both --internet-device and --hotspot-device. Skipping iptables setup.')
    else:
        if args.internet_device or args.hotspot_device:
            logger.info('Interface arguments provided but --enable-forwarding not set; leaving iptables untouched as requested.')

    if args.start_hotspot:
        handle_start_hotspot(args.start_hotspot)

    if args.set_inputs:
        if CONTROL_ONLY_MODE:
            logger.info('Skipping --set-inputs because server is running in control-only mode')
        else:
            initialize_record_section()

    if args.domain != 'localhost':
        setup_domain_hotspot_mapping(args.domain)

    if not CONTROL_ONLY_MODE:
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

    if not CONTROL_ONLY_MODE:
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

    try:
        start_playlist_thread()
    except Exception:
        logger.exception('Failed to start playlist automation thread')

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
