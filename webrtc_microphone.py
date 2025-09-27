import subprocess
import time
import logging
import re
import os
import threading

logger = logging.getLogger(__name__)

MICROPHONE_COLORS = [
    '#3357FF',  # Blue
    "#FF3434",  # Red
    '#33FF57',  # Green
    '#FFA133',  # Orange
    '#FF33A1',  # Pink
    '#A133FF',  # Purple
    '#33FFA1',  # Teal
]

MICROPHONE_COLORS_NAMES = [
    'Blue',
    'Red',
    'Green',
    'Orange',
    'Pink',
    'Purple',
    'Teal'
]





# New per-player microphone class
class WebRTCMicrophone:
    def __init__(self, player_id):
        self.player_id = player_id
        self.proc = None
        self.started_at = None
        # pulse-receive will create playback ports; we record discovered pw port ids here
        self.pw_ports = {}  # e.g. {'FL': 133, 'FR': 132}
        self.link_name = f"pulse-receive-{player_id}"
        logger.debug(f"{self.player_id}: WebRTCMicrophone initialized.")

    def start(self, offer):
        logger.debug(f"{self.player_id}: Starting WebRTC microphone with offer")
        return self.__start_new_process(offer)

    def stop(self):
        logger.debug(f"{self.player_id}: Stopping WebRTC microphone.")
        self.__stop_webrtc_process()


    def __start_new_process(self, offer):
        if not offer:
            logger.error(f"{self.player_id}: Offer must not be empty")
            return {'success': False, 'error': 'Offer must not be empty'}

        logger.debug(f"{self.player_id}: Starting new pulse-receive process via compiled binary")

        binary_path = './pulse-receive/pulse-receive'
        if not os.path.exists(binary_path) or not os.access(binary_path, os.X_OK):
            logger.error(f"{self.player_id}: pulse-receive binary not found or not executable at {binary_path}")
            return {'success': False, 'error': f'pulse-receive binary not available: {binary_path}'}

        launch_cmd = [
            binary_path,
            '--pulse-buf', '20ms',
            '--link-name', self.link_name
        ]
        logger.debug(f"{self.player_id}: Launching pulse-receive with command: {' '.join(launch_cmd)}")

        try:
            self.proc = subprocess.Popen(
                launch_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=2
            )
        except Exception as e:
            logger.exception(f"{self.player_id}: Failed to start pulse-receive process: {e}")
            self.proc = None
            return {'success': False, 'error': f'Failed to start pulse-receive: {e}'}

        self.started_at = time.time()
        # Record existing PipeWire ports before pulse-receive creates new ones
        try:
            existing_ports = self._list_pw_port_ids()
        except Exception:
            existing_ports = set()



        # Encode offer as base64-encoded JSON as required by pulse-receive
        import json, base64
        offer_obj = {"sdp": offer, "type": "offer"}
        offer_json = json.dumps(offer_obj)
        offer_b64 = base64.b64encode(offer_json.encode()).decode()

        # Write offer to session log directory as soon as possible and print to stdout
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            logs_root = os.path.join(base_dir, 'logs')
            session_dir = os.path.join(logs_root, f'session_{self.player_id}')
            os.makedirs(session_dir, exist_ok=True)
            with open(os.path.join(session_dir, 'client.offer.sdp'), 'w') as f:
                f.write(offer)
            logger.debug(f"[OFFER] session_{self.player_id} client.offer.sdp:\n{offer}\n")
        except Exception as e:
            logger.error(f"{self.player_id}: Failed to write client.offer.sdp: {e}")

        try:
            self.proc.stdin.write(offer_b64 + "\n")
            self.proc.stdin.flush()
            self.proc.stdin.close()
        except Exception:
            logger.exception(f"{self.player_id}: Failed to send offer to pulse-receive")
            return {'success': False, 'error': 'Failed to send offer to pulse-receive'}

        # Read answer from the process
        if not self.proc or not self.proc.stdout:
            logger.error(f"{self.player_id}: pulse-receive process not started correctly (no stdout)")
            return {'success': False, 'error': 'pulse-receive process not started correctly'}


        # Read and decode the base64-encoded JSON answer from pulse-receive
        import threading
        answer_b64 = ''
        answer = ''
        expect_b64 = False
        b64_buffer = []
        session_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs', f'session_{self.player_id}')

        # Helper to print all lines from a stream with a prefix
        def _print_stream_lines(stream, prefix):
            try:
                for line in iter(stream.readline, ''):
                    if not line:
                        break
                    logger.debug(f"[{prefix}][session_{self.player_id}] {line.rstrip()}")
            except Exception as e:
                logger.error(f"[{prefix}][session_{self.player_id}] <error reading stream>: {e}")

        # Start background thread to print stderr
        if self.proc and self.proc.stderr:
            threading.Thread(target=_print_stream_lines, args=(self.proc.stderr, 'GST-STDERR'), daemon=True).start()

        try:
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    time.sleep(0.1)
                    continue
                stripped = line.strip()
                logger.debug(f"[GST-STDOUT][session_{self.player_id}] {line.rstrip()}")

                if 'Connection State has changed checking' in stripped:
                    expect_b64 = True
                    b64_buffer = []
                    continue

                if expect_b64 and stripped:
                    b64_buffer.append(stripped)
                    candidate = ''.join(b64_buffer)
                    try:
                        answer_json = base64.b64decode(candidate).decode()
                        answer_obj = json.loads(answer_json)
                        answer_b64 = candidate
                        answer = answer_obj.get('sdp', '')
                        logger.info(f"{self.player_id}: Received answer from pulse-receive process")
                        # Write answer and answer_b64 as soon as available and print to stdout
                        try:
                            with open(os.path.join(session_dir, 'server.answer.sdp'), 'w') as f:
                                f.write(answer)
                            logger.debug(f"[ANSWER] session_{self.player_id} server.answer.sdp:\n{answer}\n")
                        except Exception as e:
                            logger.error(f"{self.player_id}: Failed to write server.answer.sdp: {e}")
                        try:
                            with open(os.path.join(session_dir, 'server.answer.b64.sdp'), 'w') as f:
                                f.write(answer_b64)
                            logger.debug(f"[ANSWER_B64] session_{self.player_id} server.answer.b64.sdp:\n{answer_b64}\n"  )
                        except Exception as e:
                            logger.error(f"{self.player_id}: Failed to write server.answer.b64.sdp: {e}")
                        try:
                            with open(os.path.join(session_dir, 'server.answer.decoded.sdp'), 'w') as f:
                                f.write(answer)
                        except Exception as e:
                            logger.error(f"{self.player_id}: Failed to write server.answer.decoded.sdp: {e}")
                        break
                    except Exception:
                        # Keep buffering if more base64 fragments follow
                        continue
        except Exception:
            logger.exception(f"{self.player_id}: Error reading answer from pulse-receive")
            answer = ''

        # Kick off asynchronous post-start tasks (discover pw ports) so we can return success immediately.
        try:
            threading.Thread(target=self._post_startup_tasks, args=(existing_ports,), daemon=True).start()
        except Exception:
            logger.exception(f"{self.player_id}: Failed to start post-startup thread")

        return {'success': True, 'answer': answer, 'answer_b64': answer_b64, 'player_id': self.player_id}

    def is_process_alive(self):
        """Return True if the underlying pulse-receive process appears alive.

        Checks both the subprocess state and whether the discovered PipeWire ports still exist
        (if we recorded them). This is a best-effort liveness check used by the manager monitor.
        """
        # Basic check: subprocess still running
        if not self.proc:
            return False
        try:
            if self.proc.poll() is not None:
                return False
        except Exception:
            # If poll fails, treat as dead
            return False

        # If we know pw_ports, ensure those ids are still present in pw-link
        try:
            current = self._list_pw_port_ids()
            if not current:
                # No ports listed right now; allow short-lived gap and consider process alive
                return True
            if self.pw_ports:
                # pw_ports values may be ints or lists; check presence
                for v in self.pw_ports.values():
                    if isinstance(v, (list, tuple)):
                        # at least one id from the list must still exist
                        ok = any(int(x) in current for x in v)
                        if not ok:
                            return False
                    else:
                        if int(v) not in current:
                            return False
        except Exception:
            # if pw-listing fails, don't be overly aggressive
            return True
        return True

    def __stop_webrtc_process(self):
        try:
            if self.proc:
                try:
                    self.proc.terminate()
                    self.proc.wait(timeout=1)
                except Exception:
                    try:
                        self.proc.kill()
                    except Exception:
                        pass
        finally:
            self.proc = None
            self.pw_ports = {}

    def _list_pw_ports(self, detail=False):
        """Return a dict of pw port id -> name by parsing `pw-link -I -o` output."""
        ports = {}
        try:
            proc = subprocess.run(['pw-link', '-I', '-o'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            out = proc.stdout
            link_name = getattr(self, 'link_name', 'pulse-receive')
            pattern = re.compile(r'^\s*(\d+)\s+' + re.escape(link_name) + r'(.*)$', re.IGNORECASE)
            for line in out.splitlines():
                m = pattern.match(line)
                if m:
                    pid = int(m.group(1))
                    name = m.group(2).strip()
                    ports[pid] = name
        except Exception:
            return {} if detail else set()
        return ports if detail else set(ports.keys())

    def _list_pw_port_ids(self):
        return set(self._list_pw_ports(detail=True).keys())

    def _post_startup_tasks(self, existing_ports):
        """Run after starting pulse-receive in a background thread.

        Discovers PipeWire ports created by this pulse-receive instance and
        updates self.pw_ports. This mirrors the previous blocking logic but
        runs asynchronously so start() can return immediately.
        """
        try:
            # initial delay to allow pulse-receive to enumerate ports
            attempts = 300
            new_ports = {}
            for attempt in range(attempts):
                try:
                    new_ports = self._list_pw_ports(detail=True)
                except Exception:
                    new_ports = {}
                logger.debug(f"{self.player_id}: Post-start attempt {attempt+1}/{attempts}, pw ports: {new_ports}")
                # if we got any ports, break early
                if new_ports:
                    break
                time.sleep(0.05)

            # print existing ports and new ports for debugging
            logger.debug(f"{self.player_id}: Existing ports: {existing_ports}")
            logger.debug(f"{self.player_id}: New ports: {new_ports}")
            created = {pid: name for pid, name in (new_ports or {}).items() if pid not in (existing_ports or set())}
            if created:
                # Map to channels (e.g., output_FL -> FL)
                mapping = {}
                for pid, name in created.items():
                    try:
                        lname = name.lower()
                        if 'output_fl' in lname or 'playback_fl' in lname or lname.endswith('_fl'):
                            mapping['FL'] = int(pid)
                        elif 'output_fr' in lname or 'playback_fr' in lname or lname.endswith('_fr'):
                            mapping['FR'] = int(pid)
                        else:
                            mapping.setdefault('OTHER', []).append(int(pid))
                    except Exception:
                        logger.exception(f"{self.player_id}: Error mapping pw port {pid} -> {name}")
                self.pw_ports = mapping
                logger.debug(f"{self.player_id}: Discovered pw ports: {self.pw_ports}")
            else:
                logger.warning(f"{self.player_id}: No new pw ports detected for pulse-receive; pw-link may not find this session")
        except Exception:
            logger.exception(f"{self.player_id}: Exception in post-startup tasks")
        # Attempt to auto-connect this microphone to sink 0 via the manager singleton.
        try:
            mgr = WebRTCMicrophoneManager()
            res = mgr.connect_microphone_to_sink(self.player_id, 0)
            if not res or not res.get('success'):
                logger.warning(f"{self.player_id}: Auto-connect to sink 0 failed: {res.get('error') if isinstance(res, dict) else res}")
            else:
                logger.info(f"{self.player_id}: Auto-connected to sink 0")
        except Exception:
            logger.exception(f"{self.player_id}: Exception while auto-connecting microphone to sink")

    def __check_webrtc_process(self):
        if self.proc.poll() is not None:
            logger.error(f"{self.player_id}: pulse-receive failed to start: {self.proc.poll()}")
            lines = []
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    break
                lines.append(line.strip())
                logger.error(line)
            while True:
                line = self.proc.stderr.readline()
                if not line:
                    break
                lines.append(line.strip())
                logger.error(line)
            answer_lines = lines
            return {'success': False, 'error': f"WebRTC process failed to start:\n {''.join(answer_lines)}"}
        return {'success': True}

    def get_state(self):
        if self.proc is None:
            return 'none'
        if self.proc.poll() is not None:
            return 'stopped'
        state = 'starting'
        while True:
            line = self.proc.stdout.readline()
            if not line:
                break
            if 'ICE connection state changed to' in line:
                state = line.split('ICE connection state changed to ')[-1].strip()
        return state

    def __del__(self):
        logger.debug(f"{self.player_id}: Cleaning up WebRTCMicrophone resources.")
        self.stop()


import threading

class WebRTCMicrophoneManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(WebRTCMicrophoneManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        # cleanup leftover null-sink modules from previous runs
        try:
            self.unload_all_null_sink_modules()
        except Exception:
            logger.exception('Failed to unload existing null-sink modules at startup')
        self.sink_count = 7  # 0 = lobby/null, 1-6 = game mics
        self.sink_names = [f'smartphone-mic-{i}-sink' for i in range(self.sink_count)]
        self.sinks_ready = False
        self.microphones = {}  # player_id -> WebRTCMicrophone instance
        self.source_connections = {}  # player_id -> sink_index
        self._source_lock = threading.Lock()
        # queue to serialize starting pulse-receive processes to avoid pw-link races
        self._start_queue = []  # list of player_id in FIFO order
        self._start_cond = threading.Condition()
        self.ensure_default_sinks()
        # start background monitor thread to detect dead pulse-receive processes
        try:
            self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self._monitor_thread.start()
        except Exception:
            logger.exception('Failed to start monitor thread')

    def _monitor_loop(self):
        """Background loop that periodically checks microphones and cleans up dead processes."""
        while True:
            try:
                time.sleep(5.0)
                with self._source_lock:
                    for pid, mic in list(self.microphones.items()):
                        try:
                            alive = mic.is_process_alive()
                        except Exception:
                            alive = False
                        if not alive:
                            logger.warning(f"Monitor: microphone {pid} appears dead; cleaning up")
                            try:
                                # attempt to disconnect any links first
                                try:
                                    self.disconnect_microphone(pid)
                                except Exception:
                                    logger.exception('Error while disconnecting microphone during cleanup')
                                # stop and remove process
                                mic.stop()
                            except Exception:
                                logger.exception('Failed to stop mic during cleanup')
                            # remove from maps
                            try:
                                del self.microphones[pid]
                            except Exception:
                                pass
                            try:
                                self.source_connections.pop(pid, None)
                            except Exception:
                                pass
            except Exception:
                logger.exception('Exception in monitor loop')

    def ensure_default_sinks(self):
        """Ensure all 7 sinks (lobby + 6 game mics) exist."""
        for i, sink_name in enumerate(self.sink_names):
            # Use pactl to check and create sink if missing
            try:
                result = subprocess.run(
                    ['pactl', 'list', 'short', 'sinks'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=True
                )
                if sink_name not in result.stdout:
                    logger.debug(f"Creating virtual microphone sink '{sink_name}' (index {i})")
                    create_result = subprocess.run(
                        ['pactl', 'load-module', 'module-null-sink', 'media.class=Audio/Source/Virtual', f'sink_name={sink_name}', 'channel_map=front-left,front-right'],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True
                    )
                    if create_result.returncode != 0:
                        logger.error(f"Failed to create virtual sink: {create_result.stderr.strip()}")
                        continue
                else:
                    logger.debug(f"Virtual microphone sink '{sink_name}' already exists.")
            except Exception as e:
                logger.error(f"Error ensuring sink {sink_name}: {e}")
        self.sinks_ready = True

    def unload_all_null_sink_modules(self):
        """Unload all loaded module-null-sink modules (cleanup from prior runs).

        This mimics the shell pipeline: pactl list modules | grep module-null-sink -B1 | grep -oE "#.*" | grep -oE "[0-9]+" | xargs -L1 pactl unload-module
        """
        try:
            proc = subprocess.run(['pactl', 'list', 'short', 'modules'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            out = proc.stdout or ''
            for line in out.splitlines():
                # line format: <id> <name> <argument...>
                parts = line.split()
                if len(parts) >= 2 and 'module-null-sink' in line:
                    module_id = parts[0]
                    try:
                        subprocess.run(['pactl', 'unload-module', module_id], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                        logger.debug(f"Unloaded existing null-sink module {module_id}")
                    except Exception:
                        logger.exception(f"Failed to unload module {module_id}")
        except Exception:
            logger.exception('Failed to list/unload module-null-sink modules')

    def list_sinks(self):
        """Return all sink names and indices."""
        return list(enumerate(self.sink_names))


    def start_microphone(self, player_id, offer):
        """Start a WebRTC microphone for a player (if not already running)."""
        # Enqueue the start request and wait for our turn. This serializes process
        # creation so PipeWire pw-link output ports are not created concurrently,
        # avoiding race conditions when multiple clients connect simultaneously.
        wait_timeout = 20.0  # seconds to wait for the start slot
        with self._start_cond:
            self._start_queue.append(player_id)
            start_ts = time.time()
            while True:
                # if we're at the head of the queue, proceed
                if self._start_queue and self._start_queue[0] == player_id:
                    break
                elapsed = time.time() - start_ts
                remaining = wait_timeout - elapsed
                if remaining <= 0:
                    # timed out waiting for slot; remove ourselves and return error
                    try:
                        self._start_queue.remove(player_id)
                    except Exception:
                        pass
                    return {'success': False, 'error': 'Timed out waiting to start session'}
                self._start_cond.wait(remaining)

        # At this point we are the head of the queue and may safely start the process.
        try:
            with self._source_lock:
                if player_id in self.microphones:
                    try:
                        self.microphones[player_id].stop()
                    except Exception:
                        logger.exception('Error stopping existing microphone before start')
                    try:
                        del self.microphones[player_id]
                    except Exception:
                        pass
                mic = WebRTCMicrophone(player_id)
                self.microphones[player_id] = mic
            # perform the actual start (this may invoke pw-link / wait for ports)
            return mic.start(offer)
        finally:
            # Remove ourselves from the queue and wake the next waiter
            with self._start_cond:
                try:
                    if self._start_queue and self._start_queue[0] == player_id:
                        self._start_queue.pop(0)
                    else:
                        try:
                            self._start_queue.remove(player_id)
                        except Exception:
                            pass
                except Exception:
                    pass
                try:
                    self._start_cond.notify_all()
                except Exception:
                    pass

    def remove_microphone(self, player_id):
        """Stop and remove a player's microphone and null-sink."""
        # If a start request is pending in the queue, remove it so it doesn't block others
        try:
            with self._start_cond:
                if player_id in self._start_queue:
                    try:
                        self._start_queue.remove(player_id)
                    except Exception:
                        pass
                    try:
                        self._start_cond.notify_all()
                    except Exception:
                        pass
        except Exception:
            logger.exception('Error removing pending start queue entry')

        with self._source_lock:
            mic = self.microphones.get(player_id)
            if mic:
                mic.stop()
                try:
                    del self.microphones[player_id]
                except Exception:
                    pass
            self.source_connections.pop(player_id, None)
            return {'success': True}


    def list_microphones(self):
        """Return all active microphones (player_id -> sink.monitor)."""
        return self.microphones.items()


    def connect_microphone_to_sink(self, player_id, sink_index):
        """Connect a player's microphone monitor source to a sink using pw-link."""
        if sink_index < 0 or sink_index >= self.sink_count:
            logger.error(f"connect_microphone_to_sink: Invalid sink index {sink_index}")
            return {'success': False, 'error': 'Invalid sink index'}
        mic = self.microphones.get(player_id)
        if not mic:
            logger.error(f"connect_microphone_to_sink: Microphone not found for player_id {player_id}")
            return {'success': False, 'error': 'Microphone not found'}
        monitor_source = mic.link_name
        sink_name = self.sink_names[sink_index]
        logger.debug(f"connect_microphone_to_sink: player_id={player_id}, sink_index={sink_index}, monitor_source={monitor_source}, sink_name={sink_name}")
        logger.debug(f"mic.pw_ports: {getattr(mic, 'pw_ports', None)}")
        try:
            logger.debug("Disconnecting any existing links for this microphone...")
            self.disconnect_microphone(player_id)

            logger.debug("Listing pw-link -l before linking:")
            pw_before = subprocess.run(['pw-link', '-I', '-l'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            logger.debug(f"pw-link -l output before:\n{pw_before.stdout}")

            # Prefer numeric pw port linking if available
            used_numeric = False
            if hasattr(mic, 'pw_ports') and mic.pw_ports:
                for ch in ('FL', 'FR'):
                    port_id = mic.pw_ports.get(ch)
                    if port_id:
                        target_port = f'{sink_name}:input_{ch}'
                        cmd = ['pw-link', '-w', str(port_id), target_port]
                        logger.debug(f"Attempting pw-link (numeric) for channel {ch}: {' '.join(cmd)}")
                        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                        logger.debug(f"pw-link (numeric) result: returncode={result.returncode}, stdout={result.stdout}, stderr={result.stderr}")
                        if result.returncode != 0:
                            logger.error(f"pw-link (numeric) failed for {port_id}->{target_port}: {result.stderr.strip()}")
                            # collect diagnostics
                            pw_list = subprocess.run(['pw-link', '-I', '-l'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                            logger.error(f"pw-link -l after numeric fail:\n{pw_list.stdout}")
                            return {'success': False, 'error': f"pw-link failed: {result.stderr.strip()}"}
                        used_numeric = True
                if used_numeric:
                    self.source_connections[player_id] = sink_index
                    logger.debug(
                        f"Connected {getattr(mic, 'link_name', 'pulse-receive')} numeric ports {mic.pw_ports} to {sink_name}"
                    )
                    return {'success': True}
        except Exception as e:
            pass
        logger.error(f"Error connecting monitor to sink: {e}")
        return {'success': False, 'error': str(e)}

    def disconnect_microphone(self, player_id):
        """Disconnect a player's monitor source from all sinks using pw-link -d."""
        mic = self.microphones.get(player_id)
        if not mic:
            return {'success': False, 'error': 'Microphone not found'}
        monitor_source = mic.link_name or f"pulse-receive-{player_id}"
        # If numeric pw ports were discovered for this mic, try to disconnect the exact peer ids
        # Strategy: read `pw-link -I -l`, find the line that starts with the pulse-receive output port id,
        # then scan the following lines for connection entries containing '|->' and extract the left-most id
        # from those lines. Call `pw-link -d <id>` for each extracted id.
        try:
            if getattr(mic, 'pw_ports', None):
                logger.debug(f"disconnect_microphone: removing numeric-linked peers for ports {mic.pw_ports}")
                try:
                    pw_list_proc = subprocess.run(['pw-link', '-I', '-l'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    pw_lines = pw_list_proc.stdout.splitlines()
                except Exception:
                    pw_lines = []

                for key, port_id in mic.pw_ports.items():
                    port_str = str(port_id)
                    # find the line that begins with the numeric id and mentions pulse-receive
                    idx = None
                    pattern = re.compile(r'^\s*' + re.escape(port_str) + r'\b.*pulse-receive', re.IGNORECASE)
                    for i, line in enumerate(pw_lines):
                        if pattern.match(line):
                            idx = i
                            break
                    if idx is None:
                        logger.debug(f"disconnect_microphone: port {port_id} not found in pw-link listing")
                        continue

                    # scan subsequent lines for connection entries that contain '|->' and take the left-most id
                    disconnected = set()
                    for j in range(idx + 1, len(pw_lines)):
                        line = pw_lines[j]
                        if '|->' not in line:
                            # stop scanning when we leave the connection block
                            break
                        m = re.match(r'^\s*(\d+)\b', line)
                        if m:
                            target_id = m.group(1)
                            if target_id not in disconnected:
                                try:
                                    cmd = ['pw-link', '-d', target_id]
                                    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                                    logger.debug(f"pw-link -d {target_id} -> rc={res.returncode} stderr={res.stderr.strip()}")
                                except Exception as e:
                                    logger.error(f"Error disconnecting target id {target_id}: {e}")
                                disconnected.add(target_id)
                    if not disconnected:
                        logger.debug(f"disconnect_microphone: no connected peers found for port {port_id}")
        except Exception:
            logger.exception('Error while disconnecting numeric ports')

        self.source_connections.pop(player_id, None)
        return {'success': True}
