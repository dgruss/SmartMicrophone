import subprocess
import time
import logging

logger = logging.getLogger(__name__)


class WebRTCMicrophone:
    def __init__(self, name=None):
        self.name = name
        self.proc = None

        # pulse audio stream variables
        self.sink_name = f'virt-mic-{self.name}-sink'
        self.sink_id = None
        self.source_name = f'virt-mic-{self.name}'
        self.source_id = None

        logger.info(f"{self.name}: WebRTCMicrophone initialized.")


    async def start(self, offer):
        logger.info(f"{self.name}: Starting WebRTC microphone with offer")
        # logger.debug(offer)

        if not self.name:
            logger.error(f"{self.name}: Name must not be empty")
            return {'success': False, 'error': 'Name must not be empty'}

        if self.proc is None or self.proc.poll() is not None:
            return await self.__start_new_process(offer)
        else:
            logger.info(f"{self.name}: start called but process already running, stopping and restarting.")
            self.__stop_webrtc_process()
            return await self.__start_new_process(offer)


    def stop(self):
        logger.info(f"{self.name}: Stopping WebRTC microphone.")
        self.__stop_webrtc_process()

        
    async def __start_new_process(self, offer):
        if not offer:
            logger.error(f"{self.name}: Offer must not be empty")
            return {'success': False, 'error': 'Offer must not be empty'}

        result = await self.__create_virtual_sink()
        if not result['success']:
            return result
        
        logger.debug(f"{self.name}: Starting new webrtc-cli process with offer: {offer}")

        # Start the webrtc-cli process
        self.proc = subprocess.Popen(['../webrtc-cli/webrtc-cli', 
                    '--answer', 
                    '--sink', self.sink_name,
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

        logger.info(f"{self.name}: Started webrtc-cli process")

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
        self.proc.stdout.close()

        logger.info(f"{self.name}: Received answer from webrtc-cli process")
        # logger.debug(answer)

        return {'success': True, 'answer': answer}


    def __check_webrtc_process(self):
        if self.proc.poll() is not None:
            logger.error(f"{self.name}: webrtc-cli failed to start: {self.proc.poll()}")
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
        logger.info(f"{self.name}: Stopping WebRTC process...")
        if self.proc is not None:
            try:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logger.warning(f"{self.name}: Process did not terminate in time, killing.")
                    self.proc.kill()
                    self.proc.wait(timeout=2)
            except Exception as e:
                logger.error(f"{self.name}: Error stopping process: {e}")
            finally:
                self.proc = None

        
    async def __create_virtual_sink(self):
        # Create virtual microphone sink if it doesn't exist
        sink_name = self.sink_name
        source_name = self.source_name

        logger.debug(f"{self.name}: Starting virtual sink '{sink_name}' and remap to '{source_name}'.")

        try:
            result = subprocess.run(
                ['pactl', 'list', 'short', 'modules'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True
            )
            if sink_name not in result.stdout:
                logger.info(f"{self.name}: Creating virtual microphone sink '{sink_name}' and remap to '{source_name}' source.")
                create_result = subprocess.run(
                    ['pactl', 'load-module', 'module-null-sink', f'sink_name={sink_name}'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                if create_result.returncode != 0:
                    logger.error(f"{self.name}: Failed to create virtual sink: {create_result.stderr.strip()}")
                    return {'success': False, 'error': f"Failed to create virtual sink: {create_result.stderr.strip()}"}

                self.sink_id = create_result.stdout.strip()

                
                create_result = subprocess.run(
                    ['pactl', 'load-module', 'module-remap-source', f'master={sink_name}.monitor', f'source_name={source_name}', f'source_properties=device.description="Virtual Mic {self.name}"'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                if create_result.returncode != 0:
                    logger.error(f"{self.name}: Failed to remap source: {create_result.stderr.strip()}")
                    return {'success': False, 'error': f"Failed to remap source: {create_result.stderr.strip()}"}
                
                self.source_id = create_result.stdout.strip()
                logger.debug(f"{self.name}: Created virtual microphone sink '{sink_name}' with ID {self.sink_id} and source '{source_name}' with ID {self.source_id}.")

            else:
                logger.info(f"{self.name}: Virtual microphone sink '{sink_name}' already exists.")

        except subprocess.CalledProcessError as e:
            logger.error(f"{self.name}: Error running pactl: {e.stderr.strip()}")
            return {'success': False, 'error': f"Error running pactl: {e.stderr.strip()}"}

        return {'success': True}


    def __remove_virtual_sink(self):
        logger.info(f"{self.name}: Removing virtual microphone sink '{self.sink_name}' {self.sink_id} and source '{self.source_name}' {self.source_id}.")

        if self.source_id is not None:
            subprocess.run(['pactl', 'unload-module', self.source_id])

        if self.sink_id is not None:
            subprocess.run(['pactl', 'unload-module', self.sink_id])

        logger.info(f"{self.name}: Removed virtual microphone sink '{self.sink_name}' and source '{self.source_name}'.")


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
        logger.info(f"{self.name}: Cleaning up WebRTCMicrophone resources.")
        self.__remove_virtual_sink()
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
        self.microphones = {}
        self._initialized = True


    async def add_microphone(self, name, offer):
        if name in self.microphones:
            logger.info(f"Microphone with name '{name}' already exists.")

            self.microphones[name].stop()
            return await self.microphones[name].start(offer)

        mic = WebRTCMicrophone(name)
        self.microphones[name] = mic
        return await self.microphones[name].start(offer)


    def stop_microphone(self, name):
        if name not in self.microphones:
            logger.warning(f"Tried to stop non-existent microphone '{name}'.")
            return {'success': False, 'error': f"Microphone '{name}' not found."}

        mic = self.microphones[name]
        mic.stop()
        del self.microphones[name]
        return {'success': True, 'message': f"Microphone '{name}' stopped."}


    def remove_microphone(self, name):
        mic = self.microphones.pop(name, None)
        if mic:
            mic.stop()
            del mic
            logger.info(f"Removed WebRTCMicrophone '{name}'.")
            return {'success': True}

        logger.warning(f"Tried to remove non-existent microphone '{name}'.")
        return {'success': True}

    def stop_all_microphones(self):
        logger.info("Stopping all WebRTCMicrophones...")
        for name, mic in self.microphones.items():
            mic.stop()
            del mic
            logger.info(f"Stopped microphone '{name}'.")

        self.microphones.clear()
        logger.info("All microphones stopped and cleared.")


    def get_microphone(self, name):
        return self.microphones.get(name)


    def list_microphones(self):
        return list(self.microphones.keys())