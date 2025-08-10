debugOutputField = undefined
startButton = undefined
pc = undefined

document.addEventListener('DOMContentLoaded', function() { 
    startButton = document.getElementById('buttonStartMicrophone');

    debugOutputField = document.getElementById('debugOutput');
    debugOutputField.value = '';

    document.getElementById('toggleDebug').addEventListener('click', function() {
        if (debugOutputField.style.display === 'none' || debugOutputField.style.display === '') {
            debugOutputField.style.display = 'block';
        } else {
            debugOutputField.style.display = 'none';
        }
    });
});

function setStatus(text) {
    let statusElement = document.getElementById('status');
    if (statusElement) {
        statusElement.textContent = text;
    } else {
        console.warn('Status element not found');
    }

    if (text === 'Not Connected') {
        startButton.textContent = 'Start Microphone';
        startButton.disabled = false;
    }
    else if (text === 'Connected') {
        startButton.textContent = 'Stop Microphone';
        startButton.disabled = false;
    }
    else {
        startButton.disabled = true;
    }
}

function startMicrophone() {
    if (pc && pc.iceConnectionState !== 'closed' && pc.iceConnectionState !== 'failed') {
        printLog('Session already active, stopping current session...')
        stopSession();
        setStatus('Not Connected');
        return;
    }

    createSession();
}

function createSession() {
    stopSession()

    printLog('Creating session...')

    pc = new RTCPeerConnection({
        'iceServers': [{ 'url': 'stun:stun.l.google.com:19302' }]
    })

    pc.ontrack = function(event) {
        // printLog('Accepting new track')

        // var el = document.createElement(event.track.kind)

        // el.srcObject = event.streams[0]
        // el.autoplay = true
        // el.controls = true

        // document.getElementById('tracks').appendChild(el)
    }

    pc.oniceconnectionstatechange = function(event) {
        if (pc.iceConnectionState === 'disconnected' || pc.iceConnectionState === 'failed') {
            printLog('ICE connection failed, stopping session...')
            stopSession();
            setStatus('Not Connected');
            return;
        }
        printLog('ICE connection state changed to ' + pc.iceConnectionState)
    }

    pc.addTransceiver('audio', {
        'direction': 'sendrecv',
        'sendEncodings': [{
            'maxBitrate': 16000,
            'priority': 'high',
        }],
        codecs: [{
            'mimeType': 'audio/opus',
            'clockRate': 16000,
            'channels': 1,
            'payloadType': 109,
            'sdpFmtpLine': 'maxplaybackrate=16000;stereo=0;useinbandfec=0'
        }]
    });

    mediaOpts = {
        audio: {
            autoGainControl: true,
            channelCount: 1,
            latency: 0,
            sampleRate: 16000,
            sampleSize: 16
        },
        video: false,
    }

    setStatus('Requesting microphone access...');
    navigator.mediaDevices.getUserMedia(mediaOpts).
        then(addMic).
        catch(skipMic)
}

function addMic(stream) {
    printLog('Adding microphone to session...')

    let track = stream.getTracks()[0]
    pc.addTrack(track, stream)

    createOffer()
}

function skipMic(err) {
    printLog('Skipping microphone configuration: '+err)
}

async function createOffer() {
    let offerOpts = {
        'mandatory': {
            'OfferToReceiveAudio': true,
            'OfferToReceiveVideo': false,
        },
    }

    const offer = await pc.createOffer(offerOpts);

    //offer.sdp = offer.sdp.replace('a=rtpmap:109 opus/48000/2', 'a=rtpmap:109 opus/16000/2');
    //offer.sdp = offer.sdp.replace('a=fmtp:109 maxplaybackrate=48000;stereo=1;useinbandfec=1', 
    //                              'a=fmtp:109 maxplaybackrate=16000;stereo=0;useinbandfec=0');

    pc.setLocalDescription(offer);

    console.log('Sending offer to server:', offer)

    const params = new URLSearchParams();
    params.append('action', 'start_microphone');
    params.append('offer', offer.sdp);
    params.append('name', microphoneName);

    setStatus('Connecting...');
    fetch('/api', {
        method: 'POST',
        body: params,
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
    })
    .then(response => response.json())
    .then(data => {
        printLog(JSON.stringify(data));
        if (data.success) {
            startSession(data.answer);
        } else {
            printLog('Error starting microphone: ' + data.error)
            setStatus('Error starting microphone: ' + data.error);
        }
    })
    .catch(error => {
        printLog('Network error: ' + error.message)
        setStatus('Error starting microphone: ' + data.error);
    })
}


function startSession(answer) {
    printLog('Starting session...')
    printLog('Answer: ' + answer)

    let desc = new RTCSessionDescription({
        'type': 'answer',
        'sdp': answer,
    })

    pc.setRemoteDescription(desc)
        .then(msg => {
            printLog('Session started successfully')
            setStatus('Connected');
        })
        .catch(err => {
            printLog('Error setting remote description: ' + err)
            setStatus('Error starting session: ' + err);
        });
}

function stopSession() {
    if (typeof pc === 'undefined') {
        return
    }

    printLog('Stopping session...')

    setStatus('Diconnecting...');

    const params = new URLSearchParams();
    params.append('action', 'stop_microphone');
    params.append('name', microphoneName);
    fetch('/api', {
        method: 'POST',
        body: params,
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
    })
    .then(response => response.json())
    .then(data => {
        printLog(JSON.stringify(data));
        if (data.success) {
            setStatus('Not Connected');
        } else {
            printLog('Error stopping microphone: ' + data.error)
        }
    })
    .catch(error => {
        printLog('Network error: ' + error.message)
        setStatus('Error stopping microphone: ' + data.error);
    })

    pc.close()
    pc = undefined
}

function printLog(msg) {
    console.log(msg)
    debugOutputField.value += msg + '\n'
    debugOutputField.scrollTop = debugOutputField.scrollHeight
}