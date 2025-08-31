// Debug output field (initialized on DOMContentLoaded)
let debugOutputField = null;
function printLog(msg) {
    // If textarea exists, write there; always also console.log
    if (debugOutputField) {
        debugOutputField.value += msg + '\n';
        debugOutputField.scrollTop = debugOutputField.scrollHeight;
    }
    console.log(msg);
}
// Tab switching logic
document.addEventListener('DOMContentLoaded', function() {
    // Initialize debug output and toggle button after DOM is ready
    debugOutputField = document.getElementById('debugOutput');
    if (debugOutputField) {
        debugOutputField.style.display = 'block';
        debugOutputField.style.height = '160px';
        debugOutputField.value = '';
    }
    const toggleDebugBtnInit = document.getElementById('toggleDebug');
    if (toggleDebugBtnInit && debugOutputField) {
        toggleDebugBtnInit.addEventListener('click', function() {
            debugOutputField.style.display = (debugOutputField.style.display === 'none' || debugOutputField.style.display === '') ? 'block' : 'none';
        });
    }
    printLog('Script: DOM ready');
    const tabMic = document.getElementById('tabMic');
    const tabSongs = document.getElementById('tabSongs');
    const tabControl = document.getElementById('tabControl');
    const tabSettings = document.getElementById('tabSettings');
    const mainLobby = document.getElementById('mainLobby');
    const settingsPanel = document.getElementById('settingsPanel');
    // Placeholder for future tabs
    function setActiveTab(tab) {
        [tabMic, tabSongs, tabControl, tabSettings].forEach(btn => btn && btn.classList.remove('active'));
        tab.classList.add('active');
        // Only show mainLobby for now
        mainLobby.style.display = tab === tabMic ? 'flex' : 'none';
        if (settingsPanel) settingsPanel.style.display = tab === tabSettings ? 'flex' : 'none';
        // TODO: show/hide other tab contents
    }
    tabMic.onclick = () => setActiveTab(tabMic);
    tabSongs.onclick = () => setActiveTab(tabSongs);
    tabControl.onclick = () => setActiveTab(tabControl);
    if (tabSettings) tabSettings.onclick = () => setActiveTab(tabSettings);
    setActiveTab(tabMic);
});
// other globals
startButton = undefined
pc = undefined
micAssignments = []
currentMicIndex = undefined
remoteControlUser = undefined

const MICROPHONE_COLORS = [
    '#3357FF',  // Blue
    '#FF5733',  // Red
    '#33FF57',  // Green
    '#FFA133',  // Orange
    '#FF33A1',  // Pink
    '#A133FF',  // Purple
    '#33FFA1',  // Teal
]


document.addEventListener('DOMContentLoaded', function() {
    let userName = localStorage.getItem('userName') || "";
    const nameEntry = document.getElementById('nameEntry');
    const mainLobby = document.getElementById('mainLobby');
    const userNameInput = document.getElementById('userNameInput');
    const saveNameBtn = document.getElementById('saveNameBtn');
    const micBoxes = Array.from(document.querySelectorAll('.micBox'));
    const lobbyNamesDiv = document.getElementById('lobbyNames');

    // Demo: room membership
    let rooms = {
        lobby: [],
        mic1: [],
        mic2: [],
        mic3: [],
        mic4: [],
        mic5: [],
        mic6: []
    };

    // Show/hide name entry
    if (userName) {
        nameEntry.style.display = 'none';
        mainLobby.style.display = 'flex';
        joinLobby(); // Ensure name is added to lobby on load
    } else {
        nameEntry.style.display = 'flex';
        mainLobby.style.display = 'none';
    }

    if (saveNameBtn) {
        saveNameBtn.onclick = function() {
            let val = userNameInput.value.trim();
            printLog(`Name entered: ${val}`);
            if (val.length > 0) {
                // Preserve previous room membership if present, remove old name from all records,
                // replace occurrences in micAssignments, then set new name and restore membership.
                const oldName = userName || '';
                // Find previous room (if any)
                let prevRoom = null;
                Object.keys(rooms).forEach(room => {
                    if (rooms[room].includes(oldName)) prevRoom = room;
                    // remove old name from rooms
                    rooms[room] = rooms[room].filter(u => u !== oldName);
                });
                // Replace old name in micAssignments (if present) with the new name
                if (typeof micAssignments !== 'undefined' && Array.isArray(micAssignments)) {
                    micAssignments = micAssignments.map(u => (u === oldName ? val : u));
                }

                // Set new name
                userName = val;
                localStorage.setItem('userName', userName);

                // Show lobby/main UI
                nameEntry.style.display = 'none';
                mainLobby.style.display = 'flex';

                // Restore membership: if user was in a mic room, put them back there; otherwise add to lobby
                if (prevRoom && prevRoom !== 'lobby') {
                    rooms[prevRoom].push(userName);
                } else {
                    rooms.lobby.push(userName);
                }

                updateRoomDisplays();
                updateMicDisplay();
                updateDebugBanner();
            }
        };
    } else {
        printLog('Warning: saveNameBtn not found');
    }

    // Box click logic
    micBoxes.forEach((box, idx) => {
        box.onclick = function() {
            selectMicBox(idx + 1); // mics are 1-indexed
        };
    });
    // Make lobby box clickable
    const lobbyBox = document.getElementById('lobbyBox');
    if (lobbyBox) {
        lobbyBox.style.cursor = 'pointer';
        lobbyBox.onclick = function() {
            joinLobby();
        };
    }

    function joinLobby() {
    printLog(`You joined the lobby. Current lobby users: [${rooms.lobby.join(', ')}]`);
    printLog(`Room state: ${JSON.stringify(rooms)}`);
    printLog(`Joined lobby. Rooms: ${JSON.stringify(rooms)}. Your name: ${userName}`);
        // Remove user from all rooms
        Object.keys(rooms).forEach(room => {
            rooms[room] = rooms[room].filter(u => u !== userName);
        });
        // Add to lobby
        rooms.lobby.push(userName);
        updateRoomDisplays();
    updateDebugBanner();
    }

    function selectMicBox(micNum) {
    printLog(`You joined mic${micNum}. Current mic${micNum} users: [${rooms['mic'+micNum].join(', ')}]`);
    printLog(`Room state: ${JSON.stringify(rooms)}`);
    printLog(`Joined mic${micNum}. Rooms: ${JSON.stringify(rooms)}. Your name: ${userName}`);
        // Remove user from all rooms
        Object.keys(rooms).forEach(room => {
            rooms[room] = rooms[room].filter(u => u !== userName);
        });
        // Add to selected mic room
        rooms['mic' + micNum].push(userName);
        updateRoomDisplays();
    updateDebugBanner();
    }

    function updateRoomDisplays() {
    printLog(`Room state updated: ${JSON.stringify(rooms)}`);
        // Update lobby
        lobbyNamesDiv.innerHTML = '';
        if (rooms.lobby.length === 0) {
            lobbyNamesDiv.textContent = '(No users in lobby)';
        } else {
            rooms.lobby.forEach(name => {
                let span = document.createElement('span');
                span.textContent = name;
                if (name === userName) span.style.fontWeight = 'bold';
                lobbyNamesDiv.appendChild(span);
            });
        }
        // Update mic boxes
        micBoxes.forEach((box, idx) => {
            let roomName = 'mic' + (idx + 1);
            let userSpan = box.querySelector('.micUser');
            if (rooms[roomName].length > 0) {
                box.classList.add('occupied');
                userSpan.innerHTML = rooms[roomName].map(n => n === userName ? `<strong>${n}</strong>` : n).join(', ');
            } else {
                box.classList.remove('occupied');
                userSpan.innerHTML = '';
            }
        });
        printLog(`Room state updated: ${JSON.stringify(rooms)}`);
    }

    function updateDebugBanner() {
        let banner = document.getElementById('debugBanner');
        if (!banner) {
            banner = document.createElement('div');
            banner.id = 'debugBanner';
            banner.style.position = 'fixed';
            banner.style.bottom = '10px';
            banner.style.left = '10px';
            banner.style.padding = '8px 12px';
            banner.style.background = 'rgba(0,0,0,0.7)';
            banner.style.color = '#fff';
            banner.style.borderRadius = '8px';
            banner.style.zIndex = 9999;
            document.body.appendChild(banner);
        }
        banner.textContent = `You: ${userName || '(none)'} | Lobby: [${rooms.lobby.join(', ')}] | Mic1: [${rooms.mic1.join(', ')}]`;
    }

    function updateMicDisplay() {
        micBoxes.forEach((box, idx) => {
            let user = micAssignments[idx];
            let userSpan = box.querySelector('.micUser');
            if (user) {
                        box.classList.add('occupied');
                        userSpan.innerHTML = user === userName ? `<strong>${user}</strong>` : user;
            } else {
                        box.classList.remove('occupied');
                        userSpan.innerHTML = '';
            }
        });
    }

    function updateLobbyDisplay() {
        lobbyNamesDiv.innerHTML = '';
        lobbyUsers.forEach(name => {
            let span = document.createElement('span');
            span.textContent = name;
            if (name === userName) span.style.fontWeight = 'bold';
            lobbyNamesDiv.appendChild(span);
        });
    }

    // Demo: live update (simulate other users joining/leaving)
    setInterval(() => {
        // TODO: Replace with real API polling
        updateRoomDisplays();
    }, 2000);
    // remove duplicate/leftover functions and checks
});

// Change-name button behavior: ask for current name; if correct, show name-entry for editing
document.addEventListener('DOMContentLoaded', function() {
    const changeBtn = document.getElementById('changeNameBtnSettings');
    if (!changeBtn) {
        printLog('Settings change-name button not found');
        return;
    }
    changeBtn.addEventListener('click', function() {
        let stored = localStorage.getItem('userName') || '';
        // Ask user to confirm current name
        let promptText = prompt('To change your name, please enter your current name:');
        if (promptText === null) return; // cancelled
        if (promptText.trim() === stored.trim() && stored.trim() !== '') {
            // allow editing: show name entry UI with current name prefilled
            const nameEntry = document.getElementById('nameEntry');
            const mainLobby = document.getElementById('mainLobby');
            const settingsPanel = document.getElementById('settingsPanel');
            const userNameInput = document.getElementById('userNameInput');
            if (nameEntry && userNameInput && mainLobby) {
                userNameInput.value = stored;
                nameEntry.style.display = 'flex';
                mainLobby.style.display = 'none';
                if (settingsPanel) settingsPanel.style.display = 'none';
                // focus input so user can edit immediately
                setTimeout(() => userNameInput.focus(), 50);
            } else {
                printLog('Change-name UI elements not found');
            }
        }
    });
});

function startMicrophone() {
    if (typeof currentMicIndex === 'undefined') {
        printLog('Please select a microphone first.');
        setStatus('Please select a microphone.');
        return;
    }
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

    pc.ontrack = function(event) {}

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
    params.append('index', currentMicIndex);

    setStatus('Connecting...');
    fetch('/api', {
        method: 'POST',
        body: params,
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded'
        },
        credentials: "include"
    })
    .then(response => response.json())
    .then(data => {
        printLog(JSON.stringify(data));
        if (data.success) {
            startSession(data.answer, data.index);
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


function startSession(answer, index) {
    printLog('Starting session...')
    printLog('Answer: ' + answer)

    let desc = new RTCSessionDescription({
        'type': 'answer',
        'sdp': answer,
    })
    pc.setRemoteDescription(desc)
        .then(msg => {
            printLog('Session started successfully')
            setStatus('Connected', index);
            // Update assignments after connect
            fetch('/api', {
                method: 'POST',
                body: new URLSearchParams({action: 'get_assignments'}),
                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                credentials: 'include'
            }).then(response => response.json()).then(data => {
                if (data.success) updateMicAssignments(data.assignments);
            });
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

    setStatus('Disconnecting...');

    const params = new URLSearchParams();
    params.append('action', 'stop_microphone');
    fetch('/api', {
        method: 'POST',
        body: params,
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded'
        },
        credentials: "include"
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

// Ensure a single printLog exists; other definitions earlier handle debugOutputField.