//answer = undefined

document.addEventListener('DOMContentLoaded', function() { 
    document.getElementById('offer').value = ''
    document.getElementById('answer').value = ''
});

function startMicrophone() {
    createSession();
}

function createSession() {
    stopSession()

    printLog('Creating session...')

    pc = new RTCPeerConnection({
        'iceServers': [{ 'url': 'stun:stun.l.google.com:19302' }]
    })

    pc.ontrack = function(event) {
        printLog('Accepting new track')

        var el = document.createElement(event.track.kind)

        el.srcObject = event.streams[0]
        el.autoplay = true
        el.controls = true

        document.getElementById('tracks').appendChild(el)
    }

    pc.oniceconnectionstatechange = function(event) {
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

    // actually use fetch to access /api
    const params = new URLSearchParams();
    params.append('action', 'start_microphone');
    params.append('offer', offer.sdp);
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
            startSession(data.answer);
        } else {
            printLog('Error starting microphone: ' + data.error)
        }
    })
    .catch(error => {
        printLog('Network error: ' + error.message)
    })
}


function startSession(answer) {
    // answer = document.getElementById('answer').value
    // if (answer === '') {
    //     return printLog('Error: SDP answer is not set')
    // }

    printLog('Starting session...')
    printLog('Answer: ' + answer)

    let desc = new RTCSessionDescription({
        'type': 'answer',
        'sdp': answer,
    })

    pc.setRemoteDescription(desc)
        .then(printLog)
        .catch(printLog)
}

function stopSession() {
    if (typeof pc === 'undefined') {
        return
    }

    printLog('Stopping session...')

    pc.close()
    pc = undefined
}

function printLog(msg) {
    console.log(msg)
    // log = document.getElementById('log')
    // log.value += msg + '\n'
    // log.scrollTop = log.scrollHeight
}