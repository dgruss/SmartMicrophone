import subprocess
import time
import logging

logger = logging.getLogger(__name__)

MICROPHONE_COLORS = [
    '#3357FF',  # Blue
    '#FF5733',  # Red
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




class WebRTCMicrophone:
    def __init__(self, index=None):
        self.index = index
        self.proc = None

        logger.info(f"{self.index}: WebRTCMicrophone initialized.")


    def start(self, offer):
        logger.info(f"{self.index}: Starting WebRTC microphone with offer")
        # logger.debug(offer)
        return self.__start_new_process(offer)


    def stop(self):
        logger.info(f"{self.index}: Stopping WebRTC microphone.")
        self.__stop_webrtc_process()

        
    def __start_new_process(self, offer):
        if not offer:
            logger.error(f"{self.index}: Offer must not be empty")
            return {'success': False, 'error': 'Offer must not be empty'}
        
        logger.debug(f"{self.index}: Starting new webrtc-cli process with offer: {offer}")

        # Start the webrtc-cli process
        self.proc = subprocess.Popen(['../webrtc-cli/webrtc-cli', 
                    '--answer', 
                    '--sink', f'smartphone-mic-{self.index}-sink',
                    '--mode', 'lowdelay',
                    '--rate', '48000',
                    '--pulse-buf', '5ms',
                    '--sink-frame', '5ms',
                    '--jitter-buf', '5ms',
                    '--max-drift', '5ms',
                    '--chans', '2'
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )

        time.sleep(0.1)

        check_result = self.__check_webrtc_process()
        if not check_result['success']:
            return check_result

        logger.info(f"{self.index}: Started webrtc-cli process")

        # Pipe the offer into the process
        self.proc.stdin.write(offer)
        self.proc.stdin.flush()
        self.proc.stdin.close()

        time.sleep(0.1)  # wait for the process to start and respond

        # Check if the process is still running or if it failed to start because of the offer
        check_result = self.__check_webrtc_process()
        if not check_result['success']:
            return check_result

        # read anwser from the process
        answer_lines = []
        got_full_answer = False
        while got_full_answer == False:
            line = self.proc.stdout.readline()
            if not line:
                time.sleep(0.1)
            answer_lines.append(line)

            if 'a=end-of-candidates' in line:
                got_full_answer = True

        answer = ''.join(answer_lines)

        logger.info(f"{self.index}: Received answer from webrtc-cli process")
        # logger.debug(answer)

        return {'success': True, 'answer': answer, 'index': self.index}


    def __check_webrtc_process(self):
        if self.proc.poll() is not None:
            logger.error(f"{self.index}: webrtc-cli failed to start: {self.proc.poll()}")
            lines = []
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    break
                lines.append(line.strip())
                logger.error(line, end='')  # print the line to console
            while True:
                line = self.proc.stderr.readline()
                if not line:
                    break
                lines.append(line.strip())
                logger.error(line, end='')  # print the line to console

            return {'success': False, 'error': f'WebRTC process failed to start:\n {''.join(answer_lines)}'}

        return {'success': True}


    def __stop_webrtc_process(self):
        logger.info(f"{self.index}: Stopping WebRTC process...")
        if self.proc is not None:
            try:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logger.warning(f"{self.index}: Process did not terminate in time, killing.")
                    self.proc.kill()
                    self.proc.wait(timeout=2)
            except Exception as e:
                logger.error(f"{self.index}: Error stopping process: {e}")
            finally:
                self.proc = None


    def get_state(self):
        if self.proc is None:
            return 'none'

        if self.proc.poll() is not None:
            return 'stopped'

        # check the latest state of the webrtc-cli process
        state = 'starting'
        while True:
            line = self.proc.stdout.readline()
            if not line:
                break
            
            if 'ICE connection state changed to' in line:
                state = line.split('ICE connection state changed to ')[-1].strip()

        return state


    def __del__(self):
        logger.info(f"{self.index}: Cleaning up WebRTCMicrophone resources.")
        self.stop()


class WebRTCMicrophoneManager:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(WebRTCMicrophoneManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance


    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

    
    def __del__(self):
        self.__remove_virtual_sinks()

    
    def init(self, no_microphones = 4):
        self.no_microphones = no_microphones

        # create the virtual microphones as USDX has no device plug&play support
        self.microphones = {}
        self.source_ids = {}
        self.sink_ids = {}

        for i in range(no_microphones):
            self.microphones[i] = None
            self.source_ids[i] = None
            self.sink_ids[i] = None
            self.__create_virtual_sink(i)


    def stop(self):
        self.stop_all_microphones()
        self.__remove_virtual_sinks()


    def __create_virtual_sink(self, index):
        sink_name = f'smartphone-mic-{index}-sink'
        source_name = f'smartphone-mic-{index}-source'
        # Create virtual microphone sink if it doesn't exist

        logger.debug(f"Creating virtual sink '{sink_name}' and remap to '{source_name}'.")

        try:
            result = subprocess.run(
                ['pactl', 'list', 'short', 'modules'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True
            )
            if sink_name not in result.stdout:
                logger.info(f"Creating virtual microphone sink '{sink_name}' and remap to '{source_name}' source.")
                create_result = subprocess.run(
                    ['pactl', 'load-module', 'module-null-sink', f'sink_name={sink_name}'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                if create_result.returncode != 0:
                    logger.error(f"Failed to create virtual sink: {create_result.stderr.strip()}")
                    return {'success': False, 'error': f"Failed to create virtual sink: {create_result.stderr.strip()}"}

                self.sink_ids[index] = create_result.stdout.strip()

                
                create_result = subprocess.run(
                    ['pactl', 'load-module', 'module-remap-source', f'master={sink_name}.monitor', f'source_name={source_name}', f'source_properties=device.description="Smartphone-{index}-{MICROPHONE_COLORS_NAMES[index]}"'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                if create_result.returncode != 0:
                    logger.error(f"Failed to remap source: {create_result.stderr.strip()}")
                    return {'success': False, 'error': f"Failed to remap source: {create_result.stderr.strip()}"}
                
                self.source_ids[index] = create_result.stdout.strip()
                logger.debug(f"reated virtual microphone sink '{sink_name}' with ID {self.sink_ids[index]} and source '{source_name}' with ID {self.source_ids[index]}.")

            else:
                logger.info(f"Virtual microphone sink '{sink_name}' already exists.")

                # Get the existing sink and source IDs
                sink_id = None
                source_id = None

                for line in result.stdout.splitlines():
                    if sink_name in line:
                        sink_id = line.split()[0]
                    if source_name in line:
                        source_id = line.split()[0]

                if not sink_id or not source_id:
                    logger.error(f"Could not find IDs for existing sink '{sink_name}' or source '{source_name}'.")
                    return {'success': False, 'error': 'Could not find IDs for existing sink or source.'}

                self.sink_ids[index] = sink_id
                self.source_ids[index] = source_id

                logger.info(f"    It has ids: sink_id={self.sink_ids[index]}, source_id={self.source_ids[index]}")

        except subprocess.CalledProcessError as e:
            logger.error(f"Error running pactl: {e.stderr.strip()}")
            return {'success': False, 'error': f"Error running pactl: {e.stderr.strip()}"}

        return {'success': True}


    def __remove_virtual_sinks(self):
        logger.info(f"Removing virtual microphone sinks and sources:")

        for i in range(len(self.sink_ids)):
            if self.sink_ids[i] is None:
                continue

            logger.info(f"    {self.sink_ids[i]}")
            subprocess.run(['pactl', 'unload-module', self.sink_ids[i]])
            self.sink_ids[i] = None

        for i in range(len(self.source_ids)):
            if self.source_ids[i] is None:
                continue

            logger.info(f"    {self.source_ids[i]}")
            subprocess.run(['pactl', 'unload-module', self.source_ids[i]])
            self.source_ids[i] = None

        logger.info(f"Removed virtual microphone sinks and sources.")


    def start_microphone(self, offer, index = -1):
        if index > -1:
            microphone_index = index
        else:
            # Get free microphone
            microphone_index = -1
            for i in range(self.no_microphones):
                if self.microphones[i] is None:
                    microphone_index = i
                    break
                elif self.microphones[i].get_state() == 'Not Connected':
                    microphone_index = i
                    break

        if microphone_index == -1:
            return {'success': False, 'error': 'No free microphones available.'}

        if self.microphones[microphone_index] is not None:
            self.microphones[microphone_index].stop()
            del self.microphones[microphone_index]
            self.microphones[index] = None

        mic = WebRTCMicrophone(microphone_index)
        self.microphones[microphone_index] = mic
        return mic.start(offer)


    def stop_microphone(self, index):
        if index < 0 or index >= self.no_microphones:
            return {'success': True, 'message': f"Microphone '{index}' stopped."}

        mic = self.microphones[index]
        if mic is not None:
            mic.stop()
            del self.microphones[index]
            self.microphones[index] = None

        return {'success': True, 'message': f"Microphone '{index}' stopped."}


    def stop_all_microphones(self):
        logger.info("Stopping all WebRTCMicrophones...")
        for i in range(self.no_microphones):
            self.stop_microphone(i)

        logger.info("All microphones stopped and cleared.")


    def get_microphone(self, name):
        return self.microphones.get(name)


    def list_microphones(self):
        return list(self.microphones.keys())