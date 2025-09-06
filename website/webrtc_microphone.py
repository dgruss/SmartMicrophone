import subprocess
import time
import logging
import re

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
        # webrtc-cli will create playback ports; we record discovered pw port ids here
        self.pw_ports = {}  # e.g. {'FL': 133, 'FR': 132}
        # legacy sink_name kept for compatibility with older code paths
        self.sink_name = f"smartphone-mic-src-{player_id}-sink"
        logger.info(f"{self.player_id}: WebRTCMicrophone initialized.")

    def start(self, offer):
        logger.info(f"{self.player_id}: Starting WebRTC microphone with offer")
        return self.__start_new_process(offer)

    def stop(self):
        logger.info(f"{self.player_id}: Stopping WebRTC microphone.")
        self.__stop_webrtc_process()

    def __start_new_process(self, offer):
        if not offer:
            logger.error(f"{self.player_id}: Offer must not be empty")
            return {'success': False, 'error': 'Offer must not be empty'}

        # Capture existing pw port ids so we can detect the new ports created by this process
        existing_ports = self._list_pw_port_ids()

        logger.debug(f"{self.player_id}: Starting new webrtc-cli process")
        self.proc = subprocess.Popen([
            '../webrtc-cli/webrtc-cli',
            '--answer',
            '--sink', 'smartphone-mic-0-sink',
            '--mode', 'lowdelay',
            '--rate', '48000',
            '--pulse-buf', '20ms',
            '--sink-frame', '20ms',
            '--jitter-buf', '20ms',
            '--max-drift', '0ms',
            '--chans', '2'
        ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=2
        )

        time.sleep(0.1)
        check_result = self.__check_webrtc_process()
        if not check_result['success']:
            return check_result

        logger.info(f"{self.player_id}: Started webrtc-cli process")

        # Pipe the offer into the process
        try:
            self.proc.stdin.write(offer)
            self.proc.stdin.flush()
            self.proc.stdin.close()
        except Exception:
            logger.exception(f"{self.player_id}: Failed to send offer to webrtc-cli")

        time.sleep(0.1)
        check_result = self.__check_webrtc_process()
        if not check_result['success']:
            return check_result

        # read answer from the process
        answer_lines = []
        got_full_answer = False
        while not got_full_answer:
            line = self.proc.stdout.readline()
            if not line:
                time.sleep(0.1)
                continue
            answer_lines.append(line)
            if 'a=end-of-candidates' in line:
                got_full_answer = True
        answer = ''.join(answer_lines)
        logger.info(f"{self.player_id}: Received answer from webrtc-cli process")

        # Discover pw port ids created by this webrtc-cli instance (new ports since we started)
        time.sleep(0.1)
        new_ports = self._list_pw_ports(detail=True)
        created = {pid: name for pid, name in new_ports.items() if pid not in existing_ports}
        if created:
            # Map to channels (e.g., output_FL -> FL)
            mapping = {}
            for pid, name in created.items():
                lname = name.lower()
                if 'output_fl' in lname or 'playback_fl' in lname or lname.endswith('_fl'):
                    mapping['FL'] = int(pid)
                elif 'output_fr' in lname or 'playback_fr' in lname or lname.endswith('_fr'):
                    mapping['FR'] = int(pid)
                else:
                    mapping.setdefault('OTHER', []).append(int(pid))
            self.pw_ports = mapping
            logger.info(f"{self.player_id}: Discovered pw ports: {self.pw_ports}")
        else:
            logger.warning(f"{self.player_id}: No new pw ports detected for webrtc-cli; pw-link may not find this session")
        return {'success': True, 'answer': answer, 'player_id': self.player_id}

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
            for line in out.splitlines():
                m = re.match(r'^\s*(\d+)\s+webrtc-cli(.+)$', line)
                if m:
                    pid = int(m.group(1))
                    name = m.group(2).strip()
                    ports[pid] = name
        except Exception:
            return {} if detail else set()
        return ports if detail else set(ports.keys())

    def _list_pw_port_ids(self):
        return set(self._list_pw_ports(detail=True).keys())

    def __check_webrtc_process(self):
        if self.proc.poll() is not None:
            logger.error(f"{self.player_id}: webrtc-cli failed to start: {self.proc.poll()}")
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

    def __create_null_sink(self):
        # Create per-player null-sink if not exists
        try:
            result = subprocess.run([
                'pactl', 'list', 'short', 'sinks'
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if self.sink_name not in result.stdout:
                logger.info(f"Creating null-sink '{self.sink_name}' for player {self.player_id}")
                create_result = subprocess.run([
                    'pactl', 'load-module', 'module-null-sink', 'media.class=Audio/Source/Virtual', f'sink_name={self.sink_name}', 'channel_map=front-left,front-right'
                ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if create_result.returncode != 0:
                    logger.error(f"Failed to create null-sink: {create_result.stderr.strip()}")
        except Exception as e:
            logger.error(f"Error creating null-sink: {e}")

    def __remove_null_sink(self):
        # Remove per-player null-sink
        try:
            # Find module id for this sink
            result = subprocess.run([
                'pactl', 'list', 'short', 'modules'
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for line in result.stdout.splitlines():
                if f'sink_name={self.sink_name}' in line:
                    module_id = line.split()[0]
                    subprocess.run(['pactl', 'unload-module', module_id])
                    logger.info(f"Unloaded null-sink module {module_id} for player {self.player_id}")
        except Exception as e:
            logger.error(f"Error removing null-sink: {e}")

    def get_monitor_source(self):
        # Returns the monitor source name for this player's null-sink
        return f"{self.sink_name}.monitor"

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
        logger.info(f"{self.player_id}: Cleaning up WebRTCMicrophone resources.")
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
        self.ensure_default_sinks()

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
                    logger.info(f"Creating virtual microphone sink '{sink_name}' (index {i})")
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
                    logger.info(f"Virtual microphone sink '{sink_name}' already exists.")
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
                        logger.info(f"Unloaded existing null-sink module {module_id}")
                    except Exception:
                        logger.exception(f"Failed to unload module {module_id}")
        except Exception:
            logger.exception('Failed to list/unload module-null-sink modules')

    def list_sinks(self):
        """Return all sink names and indices."""
        return list(enumerate(self.sink_names))


    def start_microphone(self, player_id, offer):
        """Start a WebRTC microphone for a player (if not already running)."""
        with self._source_lock:
            if player_id in self.microphones:
                self.microphones[player_id].stop()
                del self.microphones[player_id]
            mic = WebRTCMicrophone(player_id)
            self.microphones[player_id] = mic
            return mic.start(offer)

    def remove_microphone(self, player_id):
        """Stop and remove a player's microphone and null-sink."""
        with self._source_lock:
            mic = self.microphones.get(player_id)
            if mic:
                mic.stop()
                del self.microphones[player_id]
            self.source_connections.pop(player_id, None)
            return {'success': True}


    def list_microphones(self):
        """Return all active microphones (player_id -> sink.monitor)."""
        return {pid: mic.get_monitor_source() for pid, mic in self.microphones.items()}


    def connect_microphone_to_sink(self, player_id, sink_index):
        """Connect a player's microphone monitor source to a sink using pw-link."""
        if sink_index < 0 or sink_index >= self.sink_count:
            logger.error(f"connect_microphone_to_sink: Invalid sink index {sink_index}")
            return {'success': False, 'error': 'Invalid sink index'}
        mic = self.microphones.get(player_id)
        if not mic:
            logger.error(f"connect_microphone_to_sink: Microphone not found for player_id {player_id}")
            return {'success': False, 'error': 'Microphone not found'}
        monitor_source = mic.get_monitor_source()
        sink_name = self.sink_names[sink_index]
        logger.debug(f"connect_microphone_to_sink: player_id={player_id}, sink_index={sink_index}, monitor_source={monitor_source}, sink_name={sink_name}")
        logger.debug(f"mic.pw_ports: {getattr(mic, 'pw_ports', None)}")
        try:
            logger.debug("Disconnecting any existing links for this microphone...")
            self.disconnect_microphone(player_id)

            logger.debug("Listing pw-link -l before linking:")
            pw_before = subprocess.run(['pw-link', '-I', '-l'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            logger.debug(f"pw-link -l output before:\n{pw_before.stdout}")

            logger.debug("Listing pactl sinks and sources before linking:")
            pactl_sinks = subprocess.run(['pactl', 'list', 'short', 'sinks'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            pactl_sources = subprocess.run(['pactl', 'list', 'short', 'sources'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            logger.debug(f"pactl sinks:\n{pactl_sinks.stdout}")
            logger.debug(f"pactl sources:\n{pactl_sources.stdout}")

            # Wait a short while for ports to appear in pw-link/pactl (race on creation)
            ok, diag = self._wait_for_ports(monitor_source, sink_name, timeout=3.0)
            if not ok:
                logger.warning(f"Ports not visible before linking: {diag}")

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
                    logger.info(f"Connected webrtc-cli numeric ports {mic.pw_ports} to {sink_name}")
                    return {'success': True}

            # Fallback: use monitor_source name
            cmd = [
                'pw-link',
                f'{monitor_source}:output',
                f'{sink_name}:input'
            ]
            logger.debug(f"Attempting pw-link (named): {' '.join(cmd)}")
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            logger.debug(f"pw-link (named) result: returncode={result.returncode}, stdout={result.stdout}, stderr={result.stderr}")
            if result.returncode != 0:
                # collect diagnostics to help debugging
                pw_list = subprocess.run(['pw-link', '-I', '-l'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                pactl_sinks2 = subprocess.run(['pactl', 'list', 'short', 'sinks'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                pactl_sources2 = subprocess.run(['pactl', 'list', 'short', 'sources'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                logger.error(f"pw-link (named) failed: {result.stderr.strip()}\npw-link -l:\n{pw_list.stdout}\nSinks:\n{pactl_sinks2.stdout}\nSources:\n{pactl_sources2.stdout}")
                return {'success': False, 'error': f"pw-link failed: {result.stderr.strip()}"}
            self.source_connections[player_id] = sink_index
            logger.info(f"Connected {monitor_source} to {sink_name} (named fallback)")
            return {'success': True}
        except Exception as e:
            logger.error(f"Error connecting monitor to sink: {e}")
            return {'success': False, 'error': str(e)}

    def _wait_for_ports(self, monitor_source, sink_name, timeout=3.0):
        """Wait up to `timeout` seconds for monitor_source:output and sink_name:input to appear in pw-link -l or pactl lists.

        Returns (True, '') if both present, or (False, diagnostics) if not.
        """
        deadline = time.time() + timeout
        last_pw = ''
        while time.time() < deadline:
            try:
                pw = subprocess.run(['pw-link', '-l'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                last_pw = pw.stdout
                out = pw.stdout
                want_out = f"{monitor_source}:output"
                want_in = f"{sink_name}:input"
                if want_out in out and want_in in out:
                    return True, ''
            except Exception:
                pass
            # also check pactl lists as a fallback
            try:
                sinks = subprocess.run(['pactl', 'list', 'short', 'sinks'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                sources = subprocess.run(['pactl', 'list', 'short', 'sources'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if monitor_source in sources.stdout and sink_name in sinks.stdout:
                    return True, ''
            except Exception:
                pass
            time.sleep(0.2)
        diag = f"pw-link -l:\n{last_pw}\n(pactl sinks/sources not matching)"
        return False, diag

    def disconnect_microphone(self, player_id):
        """Disconnect a player's monitor source from all sinks using pw-link -d."""
        mic = self.microphones.get(player_id)
        if not mic:
            return {'success': False, 'error': 'Microphone not found'}
        monitor_source = mic.get_monitor_source()
        # If numeric pw ports were discovered for this mic, try to disconnect the exact peer ids
        # Strategy: read `pw-link -I -l`, find the line that starts with the webrtc-cli output port id,
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
                    # find the line that begins with the numeric id and mentions webrtc-cli
                    idx = None
                    pattern = re.compile(r'^\s*' + re.escape(port_str) + r'\b.*webrtc-cli', re.IGNORECASE)
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

        # Fallback: also disconnect monitor source name from sink inputs
        for sink_name in self.sink_names:
            try:
                cmd = [
                    'pw-link', '-d',
                    f'{monitor_source}:output',
                    f'{sink_name}:input'
                ]
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                logger.debug(f"pw-link -d {monitor_source}:output {sink_name}:input -> rc={res.returncode} stderr={res.stderr.strip()}")
            except Exception as e:
                logger.error(f"Error disconnecting {monitor_source} from {sink_name}: {e}")
        self.source_connections.pop(player_id, None)
        return {'success': True}
