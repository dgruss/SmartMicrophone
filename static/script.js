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
    // Request fullscreen helper (tries standard API and vendor-prefixed variants)
    function requestFullscreenIfPossible() {
        try {
            const el = document.documentElement || document.body;
            if (!el) return;
            if (el.requestFullscreen) {
                el.requestFullscreen().catch(() => {});
            } else if (el.webkitRequestFullscreen) {
                el.webkitRequestFullscreen();
            } else if (el.mozRequestFullScreen) {
                el.mozRequestFullScreen();
            } else if (el.msRequestFullscreen) {
                el.msRequestFullscreen();
            }
        } catch (e) {}
    }
    // Initialize debug output and toggle button after DOM is ready
    debugOutputField = document.getElementById('debugOutput');
    if (debugOutputField) {
        // keep hidden by default; only show when user toggles inside Settings
        debugOutputField.style.display = 'none';
        debugOutputField.style.height = '160px';
        debugOutputField.value = '';
    }
    const toggleDebugBtnInit = document.getElementById('toggleDebug');
    if (toggleDebugBtnInit && debugOutputField) {
        toggleDebugBtnInit.addEventListener('click', function() {
            // Only allow toggling when Settings panel is visible
            const settingsPanelEl = document.getElementById('settingsPanel');
            if (settingsPanelEl && settingsPanelEl.style.display !== 'flex') return;
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

// Ensure tab switching shows the correct panels (songs/control/settings)
document.addEventListener('DOMContentLoaded', function() {
    const tabMic = document.getElementById('tabMic');
    const tabSongs = document.getElementById('tabSongs');
    const tabControl = document.getElementById('tabControl');
    const tabSettings = document.getElementById('tabSettings');
    const mainLobby = document.getElementById('mainLobby');
    const songsPanel = document.getElementById('songsPanel');
    const controlPanel = document.getElementById('controlPanel');
    const settingsPanel = document.getElementById('settingsPanel');

    // track active tab for auto-release behavior
    let activeTab = tabMic;

    function showTab(tab) {
        [tabMic, tabSongs, tabControl, tabSettings].forEach(btn => btn && btn.classList.remove('active'));
        if (tab) tab.classList.add('active');

        // show/hide panels
        if (mainLobby) mainLobby.style.display = (tab === tabMic) ? 'flex' : 'none';
        if (songsPanel) songsPanel.style.display = (tab === tabSongs) ? 'flex' : 'none';
        if (controlPanel) controlPanel.style.display = (tab === tabControl) ? 'flex' : 'none';
        if (settingsPanel) settingsPanel.style.display = (tab === tabSettings) ? 'flex' : 'none';

        // auto-acquire/release when switching to/from Control
        if (activeTab !== tab) {
            // leaving control tab -> release
            if (activeTab === tabControl) {
                try { window.releaseControl && window.releaseControl(); } catch(e){}
            }
            // entering control tab -> acquire
            if (tab === tabControl) {
                try { window.acquireControl && window.acquireControl(); } catch(e){}
            }
            activeTab = tab;
        }
    }

    if (tabMic) tabMic.addEventListener('click', () => showTab(tabMic));
    if (tabSongs) tabSongs.addEventListener('click', () => showTab(tabSongs));
    if (tabControl) tabControl.addEventListener('click', () => showTab(tabControl));
    if (tabSettings) tabSettings.addEventListener('click', () => showTab(tabSettings));
    // initialize
    showTab(tabMic);
});

window.addEventListener('beforeunload', function (e) {
  if (navigator.sendBeacon) {
    navigator.sendBeacon('/api/disconnect');
  } else {
    // Fallback for older browsers
    fetch('/api/disconnect', {method: 'POST', keepalive: true});
  }
});

// Songs UI logic
document.addEventListener('DOMContentLoaded', function() {
    const songsPanel = document.getElementById('songsPanel');
    const tabSongs = document.getElementById('tabSongs');
    const songSearchInput = document.getElementById('songSearchInput');
    const songSearchBtn = document.getElementById('songSearchBtn');
    const songClearBtn = document.getElementById('songClearBtn');
    const songResults = document.getElementById('songResults');
    const songPreview = document.getElementById('songPreview');
    // Shared audio player state so only one preview plays at a time
    let currentAudio = null;
    let currentPlayingId = null;
    let currentPlayButton = null;

    // stop and fully cleanup the current audio and its UI
    function stopCurrentAudio() {
        try {
            if (window._currentAudio) {
                const a = window._currentAudio;
                // remove bound handlers
                try { if (a._boundTimeUpdate) a.removeEventListener('timeupdate', a._boundTimeUpdate); } catch(e){}
                try { if (a._boundEnded) a.removeEventListener('ended', a._boundEnded); } catch(e){}
                try { if (a._boundError) a.removeEventListener('error', a._boundError); } catch(e){}
                try { if (a._boundLoadedMetadata) a.removeEventListener('loadedmetadata', a._boundLoadedMetadata); } catch(e){}
                try { a.pause(); } catch(e){}
                try { a.src = ''; } catch(e){}
                try { delete a._boundTimeUpdate; } catch(e){}
                try { delete a._boundEnded; } catch(e){}
                try { delete a._boundError; } catch(e){}
                try { delete a._boundLoadedMetadata; } catch(e){}
            }
        } catch(e) {
            // ignore
        }
        // clear UI
        try { if (window._currentPlayButton) window._currentPlayButton.textContent = '▶'; } catch(e){}
        try { if (window._currentSlider) { window._currentSlider.style.display = 'none'; window._currentSlider.max = 0; window._currentSlider.value = 0; window._currentSlider.oninput = null; } } catch(e){}
        // unset globals
        try { window._currentAudio = null; } catch(e){}
        try { window._currentSlider = null; } catch(e){}
        try { window._currentPlayButton = null; } catch(e){}
        currentAudio = null;
        currentPlayButton = null;
        currentPlayingId = null;
    }

    function renderResults(items) {
        songResults.innerHTML = '';
        if (!items || items.length === 0) {
            songResults.textContent = 'No songs found';
            return;
        }
        items.forEach(it => {
            const row = document.createElement('div');
            row.style.display = 'flex';
            row.style.justifyContent = 'space-between';
            row.style.alignItems = 'center';
            row.style.padding = '6px 8px';
            row.style.borderBottom = '1px solid #f0f0f0';

            const title = document.createElement('div');
            title.textContent = it.display;
            title.style.flex = '1';

            const actions = document.createElement('div');
            actions.style.flex = '0 0 auto';
            actions.style.display = 'flex';
            actions.style.gap = '8px';

            // small play/pause control per-row + position slider
            const previewBtn = document.createElement('button');
            previewBtn.textContent = '▶';
            previewBtn.title = 'Preview';
            previewBtn.style.width = '52px';
            previewBtn.style.height = '28px';
            previewBtn.style.padding = '2px 6px';

            // position slider and time labels
            const slider = document.createElement('input');
            slider.type = 'range';
            slider.min = 0;
            slider.max = 0;
            slider.value = 0;
            slider.style.width = '52px';
            slider.style.display = 'none';
            slider.title = 'Seek';

            // helper to format seconds -> M:SS
            function fmt(t) {
                if (!isFinite(t) || t < 0) return '0:00';
                const m = Math.floor(t / 60);
                const s = Math.floor(t % 60).toString().padStart(2, '0');
                return m + ':' + s;
            }

            // Keep references to the slider/time for the currently playing audio
            let boundTimeUpdate = null;

            previewBtn.onclick = () => {
                // Use server-side id mapping for preview to avoid exposing file paths
                if (!it.id) {
                    printLog('Preview id missing for item: ' + JSON.stringify(it));
                    return;
                }
                const url = '/songs/preview?id=' + encodeURIComponent(it.id);
                // If this row is already playing, stop it
                if (currentPlayingId === it.id) {
                    stopCurrentAudio();
                    return;
                }

                // Stop previous audio if any and clear its UI
                if (currentAudio || window._currentAudio) {
                    stopCurrentAudio();
                }

                // Create new Audio and play
                currentAudio = new Audio(url);
                // store globals so other rows can clear them
                window._currentAudio = currentAudio;
                window._currentSlider = slider;
                window._currentPlayButton = previewBtn;

                currentPlayingId = it.id;
                currentPlayButton = previewBtn;
                previewBtn.textContent = '⏸';

                // show slider
                slider.style.display = 'inline-block';

                // when metadata loads, set slider max and duration
                const onLoaded = () => {
                    try {
                        const dur = isFinite(currentAudio.duration) ? Math.floor(currentAudio.duration) : 0;
                        slider.max = dur;
                    } catch (e) {}
                };
                currentAudio._boundLoadedMetadata = onLoaded;
                currentAudio.addEventListener('loadedmetadata', onLoaded);

                // timeupdate updates slider
                boundTimeUpdate = () => {
                    try {
                        const t = Math.floor(currentAudio.currentTime || 0);
                        slider.value = t;
                    } catch (e) {}
                };
                currentAudio._boundTimeUpdate = boundTimeUpdate;
                currentAudio.addEventListener('timeupdate', boundTimeUpdate);

                const onEnded = () => { stopCurrentAudio(); };
                currentAudio._boundEnded = onEnded;
                currentAudio.addEventListener('ended', onEnded);

                const onError = (ev) => {
                    printLog('Preview error for ' + (it.mp3 || it.display || it.id));
                    stopCurrentAudio();
                };
                currentAudio._boundError = onError;
                currentAudio.addEventListener('error', onError);

                // seeking via slider
                slider.oninput = (ev) => {
                    if (currentAudio && slider.max > 0) {
                        try { currentAudio.currentTime = Number(slider.value); } catch(e) {}
                    }
                };

                currentAudio.play().catch(err => {
                    printLog('Preview play failed: ' + err);
                    stopCurrentAudio();
                });
            };

            const addBtn = document.createElement('button');
            addBtn.textContent = it.upl ? 'Remove' : 'Add';
            addBtn.style.minWidth = '64px';
            addBtn.onclick = () => {
                if (!it.id) { printLog('Cannot modify upl: missing id'); return; }
                const action = it.upl ? 'remove' : 'add';
                addBtn.disabled = true;
                fetch('/songs/add_to_upl', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({id: it.id, action: action}),
                    credentials: 'include'
                }).then(r=>r.json()).then(data=>{
                    addBtn.disabled = false;
                    if (data && data.success) {
                        it.upl = !!data.upl;
                        addBtn.textContent = it.upl ? 'Remove' : 'Add';
                        printLog((it.upl ? 'Added' : 'Removed') + ' from upl: ' + (data.line || it.display));
                    } else {
                        printLog('Failed to modify upl: ' + (data && data.error ? data.error : 'unknown'));
                    }
                }).catch(e=>{ addBtn.disabled = false; printLog('Network error modifying upl: '+e); });
            };

            // place preview button, slider, time and add button together
            const previewContainer = document.createElement('div');
            previewContainer.style.display = 'flex';
            previewContainer.style.alignItems = 'center';
            previewContainer.style.gap = '8px';
            previewContainer.appendChild(previewBtn);
            previewContainer.appendChild(slider);

            actions.appendChild(previewContainer);
            actions.appendChild(addBtn);
            row.appendChild(title);
            row.appendChild(actions);
            songResults.appendChild(row);
        });
    }

    function searchSongs(q) {
        songResults.textContent = 'Searching...';
        fetch('/songs/search?q=' + encodeURIComponent(q) + '&per_page=100')
            .then(r=>r.json()).then(data=>{
                if (data.success) renderResults(data.items);
                else songResults.textContent = 'Search failed';
            }).catch(e=>{ songResults.textContent = 'Network error'; printLog('Search error: '+e); });
    }

    if (songSearchBtn) songSearchBtn.onclick = () => searchSongs(songSearchInput.value.trim());
    if (songClearBtn) songClearBtn.onclick = () => { songSearchInput.value=''; songResults.innerHTML=''; songPreview.style.display='none'; };
    // allow pressing Enter in the search input to trigger search
    if (songSearchInput) {
        songSearchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                if (songSearchBtn) songSearchBtn.click();
            }
        });
    }

    // When Songs tab is activated, ensure panel shows
    if (tabSongs) tabSongs.addEventListener('click', () => {
        if (songsPanel) songsPanel.style.display = 'flex';
    });
});
// other globals
startButton = undefined
pc = undefined
micAssignments = []
currentMicIndex = undefined
remoteControlUser = undefined

let currentRoom = 'lobby';
let desiredRoom = currentRoom;
let autoRejoinSuppressedUntil = 0;
let lastServerReportedRoom = null;
try {
    currentRoom = localStorage.getItem('currentRoom') || 'lobby';
    desiredRoom = currentRoom;
} catch (e) {}

const MICROPHONE_COLORS = [
    '#3357FF',  // Blue
    '#FF3434',  // Red
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

    function suppressAutoRoomRejoin(ms = 4000) {
        autoRejoinSuppressedUntil = Date.now() + Math.max(0, ms);
    }

    function rememberCurrentRoom(roomName, opts = {}) {
        currentRoom = roomName || 'lobby';
        desiredRoom = currentRoom;
        if (opts.suppress) {
            suppressAutoRoomRejoin(opts.suppress);
        }
        try {
            localStorage.setItem('currentRoom', currentRoom);
        } catch (e) {}
    }

    function findRoomContainingSelf(snapshot) {
        if (!userName) return null;
        const source = snapshot || rooms || {};
        for (const [roomName, members] of Object.entries(source)) {
            if (Array.isArray(members) && members.includes(userName)) {
                return roomName;
            }
        }
        return null;
    }

    rememberCurrentRoom(currentRoom || 'lobby');

    async function attemptServerJoin(targetRoom, options = {}) {
        const roomName = targetRoom || 'lobby';
        const { silent = false } = options;
        if (!userName) {
            if (!silent) {
                printLog('Please set your name before joining a room.');
            }
            return false;
        }
        desiredRoom = roomName;
        suppressAutoRoomRejoin(options.suppressMs || 4000);
        if (!silent) {
            printLog(`Attempting to join ${roomName} as ${userName}`);
        }
        let delayVal = 0;
        try {
            delayVal = parseInt(localStorage.getItem('playerDelayMs') || '0') || 0;
        } catch (e) {}
        try {
            const res = await fetch('/rooms/join', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                credentials: 'include',
                body: JSON.stringify({room: roomName, name: userName, delay: delayVal})
            });
            const data = await res.json();
            if (data && data.success) {
                rooms = data.rooms || rooms;
                if (data.name) {
                    userName = data.name;
                    localStorage.setItem('userName', userName);
                }
                const serverRoom = data.room || roomName;
                rememberCurrentRoom(serverRoom, {suppress: 1500});
                lastServerReportedRoom = serverRoom;
                if (!silent) {
                    printLog(`Joined ${serverRoom} (server). Rooms: ${JSON.stringify(rooms)}. Your name: ${userName}`);
                }
                updateRoomDisplays();
                return true;
            } else if (!silent) {
                printLog('Server join failed: ' + (data && data.error));
            }
        } catch (e) {
            if (!silent) {
                printLog('Server join error: ' + e);
            }
        }
        return false;
    }

    let membershipCheckPromise = null;
    function ensureRoomMembership(source) {
        if (!userName || !currentRoom) return;
        const now = Date.now();
        if (now < autoRejoinSuppressedUntil) return;
        const members = rooms[currentRoom] || [];
        if (members.includes(userName)) return;
        const locatedRoom = findRoomContainingSelf(rooms);
        if (locatedRoom) {
            if (locatedRoom !== currentRoom) {
                rememberCurrentRoom(locatedRoom);
            }
            return;
        }
        if (membershipCheckPromise) return;
        const target = desiredRoom || currentRoom || 'lobby';
        membershipCheckPromise = attemptServerJoin(target, {silent: true, suppressMs: 2000}).finally(() => {
            membershipCheckPromise = null;
        });
    }

    // Show/hide name entry
    if (userName) {
        nameEntry.style.display = 'none';
        mainLobby.style.display = 'flex';
        attemptServerJoin(currentRoom || 'lobby', {silent: true}); // Ensure server keeps us in our last room
        // Start WebRTC session for this client (capture mic, send offer to server)
        try {
            if (!window.smartMicPC) {
                startWebRTCSession().catch(e => printLog('WebRTC start failed: ' + e));
            }
        } catch (e) {
            printLog('Error starting WebRTC: ' + e);
        }
        // Try to request fullscreen on page load (may be blocked if not a user gesture)
        try {
            requestFullscreenIfPossible();
        } catch (e) {}
    } else {
        nameEntry.style.display = 'flex';
        mainLobby.style.display = 'none';
    }

    if (saveNameBtn) {
        saveNameBtn.onclick = async function() {
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

                // Request fullscreen after the user clicked Continue (user gesture)
                try { requestFullscreenIfPossible(); } catch (e) {}

                // Restore membership: if user was in a mic room, put them back there; otherwise add to lobby
                if (prevRoom && prevRoom !== 'lobby') {
                    rooms[prevRoom].push(userName);
                } else {
                    rooms.lobby.push(userName);
                }

                updateRoomDisplays();
                updateMicDisplay();
                if (prevRoom && prevRoom.startsWith('mic')) {
                    const micNum = parseInt(prevRoom.slice(3), 10);
                    if (!Number.isNaN(micNum)) {
                        await selectMicBox(micNum);
                        return;
                    }
                }
                await joinLobby();
            }
        };
    } else {
        printLog('Warning: saveNameBtn not found');
    }
    // Load audio settings from localStorage and initialize checkboxes
    try {
        const ns = localStorage.getItem('optNoiseSuppression') === 'true';
        const ec = localStorage.getItem('optEchoCancellation') === 'true';
        const ag = localStorage.getItem('optAutoGain') === 'true';
        const nsEl = document.getElementById('optNoiseSuppression');
        const ecEl = document.getElementById('optEchoCancellation');
        const agEl = document.getElementById('optAutoGain');
        if (nsEl) { nsEl.checked = ns; nsEl.addEventListener('change', () => { localStorage.setItem('optNoiseSuppression', nsEl.checked); }); }
        if (ecEl) { ecEl.checked = ec; ecEl.addEventListener('change', () => { localStorage.setItem('optEchoCancellation', ecEl.checked); }); }
        if (agEl) { agEl.checked = ag; agEl.addEventListener('change', () => { localStorage.setItem('optAutoGain', agEl.checked); }); }
    } catch (e) { }
    // Initialize delay control
    try {
        const delayKey = 'playerDelayMs';
        let delay = parseInt(localStorage.getItem(delayKey) || '0') || 0;
        const disp = document.getElementById('delayDisplay');
        const plus = document.getElementById('delayPlus');
        const minus = document.getElementById('delayMinus');
        function renderDelay() { if (disp) disp.textContent = `${delay} ms`; }
        renderDelay();

        // network send helper (POSTs updated delay to server)
        async function sendPlayerDelay(newDelay) {
            try {
                await fetch('/player/delay', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    credentials: 'include',
                    body: JSON.stringify({delay: newDelay})
                });
            } catch (e) { /* ignore network errors for now */ }
        }

        // Hold-to-repeat behavior for +/- buttons.
        const MIN_DELAY = -1000;
        const MAX_DELAY = 1000;
        let lastSentDelay = parseInt(localStorage.getItem(delayKey) || '0') || 0;

        function clamp(v) {
            return Math.min(MAX_DELAY, Math.max(MIN_DELAY, v));
        }

        function setupHoldButton(elem, delta) {
            if (!elem) return;
            let intervalId = null;
            let active = false;

            const doStep = () => {
                delay = clamp(delay + delta);
                localStorage.setItem(delayKey, String(delay));
                renderDelay();
            };

            const start = (ev) => {
                ev.preventDefault();
                if (active) return;
                active = true;
                // take one immediate step on press
                doStep();
                // then continue stepping every 100ms while held
                intervalId = setInterval(doStep, 100);
            };

            const stop = (/*ev*/) => {
                if (!active) return;
                active = false;
                if (intervalId) { clearInterval(intervalId); intervalId = null; }
                // Only send if value changed since last send
                if (delay !== lastSentDelay) {
                    lastSentDelay = delay;
                    sendPlayerDelay(delay);
                }
            };

            // mouse events
            elem.addEventListener('mousedown', start);
            elem.addEventListener('mouseup', stop);
            elem.addEventListener('mouseleave', stop);
            // touch events
            elem.addEventListener('touchstart', start, {passive:false});
            elem.addEventListener('touchend', stop);
            elem.addEventListener('touchcancel', stop);
            // Ensure release anywhere stops repeating
            document.addEventListener('mouseup', stop);
            document.addEventListener('touchend', stop);
            document.addEventListener('touchcancel', stop);
        }

        setupHoldButton(plus, +10);
        setupHoldButton(minus, -10);
    } catch (e) {}
    // allow pressing Enter in the name input to save
    if (userNameInput) {
        userNameInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                if (saveNameBtn) saveNameBtn.click();
            }
        });
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

    async function joinLobby(options = {}) {
        const { silent = false } = options;
        const success = await attemptServerJoin('lobby', {silent});
        if (success) return;

        if (!silent) {
            printLog(`You joined the lobby. Current lobby users: [${rooms.lobby.join(', ')}]`);
        }
        Object.keys(rooms).forEach(room => { rooms[room] = rooms[room].filter(u => u !== userName); });
        rooms.lobby.push(userName);
        rememberCurrentRoom('lobby');
        updateRoomDisplays();
    }

    async function selectMicBox(micNum, options = {}) {
        const { silent = false } = options;
        const target = 'mic' + micNum;
        const success = await attemptServerJoin(target, {silent});
        if (success) return;

        // Fallback to local-only behavior if server not available
        Object.keys(rooms).forEach(room => { rooms[room] = rooms[room].filter(u => u !== userName); });
        rooms[target].push(userName);
        rememberCurrentRoom(target);
        updateRoomDisplays();
    }

    function updateRoomDisplays() {
    //printLog(`Room state updated: ${JSON.stringify(rooms)}`);
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
        //printLog(`Room state updated: ${JSON.stringify(rooms)}`);
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

    // Unified status polling (rooms + control) every 2s. This also acts as a heartbeat
    // so the server can detect dead clients. If the client doesn't poll for >10s,
    // the server will disconnect its webrtc session.
    async function pollStatus() {
        try {
            const res = await fetch('/status', {credentials: 'include'});
            const data = await res.json();
            if (data && data.success) {
                if (data.rooms) {
                    rooms = data.rooms;
                    updateRoomDisplays();
                    ensureRoomMembership('poll');
                }
                if (data.you) {
                    const reported = data.you.room || null;
                    if (reported) {
                        lastServerReportedRoom = reported;
                        const now = Date.now();
                        const serverMatchesDesired = reported === desiredRoom;
                        if ((now >= autoRejoinSuppressedUntil) || serverMatchesDesired) {
                            if (reported !== currentRoom) {
                                rememberCurrentRoom(reported);
                            }
                        }
                    }
                }
                if (data.control) {
                    controlOwner = data.control.owner;
                    controlName = data.control.owner_name;
                    // update control UI if the control tab is present
                    try { updateControlUI(); } catch (e) {}
                }
            }
        } catch (e) {
            // ignore network errors and keep local state
            // keep trying; server will disconnect stale sessions after 10s
        }
    }
    // initial poll and interval
    pollStatus();
    setInterval(pollStatus, 2000);

    // Start a WebRTC session with the server: create RTCPeerConnection, capture local mic,
    // send SDP offer to server (/api?action=start_webrtc) and apply returned SDP answer.
    async function startWebRTCSession() {
        printLog('Starting WebRTC session...');
        const pc = new RTCPeerConnection();
        window.smartMicPC = pc;

        // Send local ICE candidates to server if needed (server-side pulse-receive may not need them)
        pc.onicecandidate = (ev) => {
            // we do not currently send candidates to server; server's pulse-receive is expected to be
            // the offer/answer terminator. If needed, implement trickle ICE here.
            if (!ev.candidate) return;
            printLog('Local ICE candidate: ' + JSON.stringify(ev.candidate));
        };

        // Optionally attach remote tracks to an <audio> element for monitoring
        pc.ontrack = (ev) => {
            // Received remote track; create a hidden audio element (no controls) so audio plays
            printLog('Received remote track (hidden monitor)');
            let au = document.getElementById('remoteMonitor');
            if (!au) {
                au = document.createElement('audio');
                au.id = 'remoteMonitor';
                au.autoplay = true;
                // do not add controls or append to DOM to avoid visual overlay
                au.style.display = 'none';
                document.body.appendChild(au);
            }
            au.srcObject = ev.streams && ev.streams[0] ? ev.streams[0] : new MediaStream(ev.track ? [ev.track] : []);
        };

        // Get local microphone and add to PeerConnection
        try {
            // build audio constraints from saved settings
            const audioConstraints = {};
            try {
                const ns = localStorage.getItem('optNoiseSuppression') === 'true';
                const ec = localStorage.getItem('optEchoCancellation') === 'true';
                const ag = localStorage.getItem('optAutoGain') === 'true';
                audioConstraints.noiseSuppression = !!ns;
                audioConstraints.echoCancellation = !!ec;
                audioConstraints.autoGainControl = !!ag;
            } catch (e) {}
            const localStream = await navigator.mediaDevices.getUserMedia({audio: audioConstraints, video: false});
            localStream.getTracks().forEach(t => pc.addTrack(t, localStream));
        } catch (e) {
            printLog('getUserMedia failed: ' + e);
            // proceed without local audio if permission denied
        }

        // monitor ICE connection state for automatic reconnect attempts
        pc.addEventListener('iceconnectionstatechange', () => {
            printLog('PC iceConnectionState: ' + pc.iceConnectionState);
            if (pc.iceConnectionState === 'disconnected' || pc.iceConnectionState === 'failed') {
                // schedule a reconnect attempt
                tryScheduleReconnect();
            }
        });

        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);

        // send offer to server
        const form = new FormData();
        form.append('action', 'start_webrtc');
        form.append('offer', offer.sdp);

        const res = await fetch('/api', {method: 'POST', body: form, credentials: 'include'});
        const data = await res.json();
        if (!data || !data.success) {
            throw new Error(data && data.error ? data.error : 'Failed to start webrtc on server');
        }
        const answerSDP = data.answer;
        if (!answerSDP) throw new Error('No SDP answer from server');

        const answer = {type: 'answer', sdp: answerSDP};
        await pc.setRemoteDescription(answer);
        printLog('WebRTC session started successfully');
        return pc;
    }

    // Reconnect logic with exponential backoff
    let _reconnectAttempts = 0;
    let _reconnectTimer = null;
    function tryScheduleReconnect() {
        if (_reconnectTimer) return; // already scheduled
        _reconnectAttempts = Math.min(6, _reconnectAttempts + 1);
        const delay = Math.min(60, Math.pow(2, _reconnectAttempts));
        printLog('Scheduling reconnect attempt in ' + delay + 's (attempt ' + _reconnectAttempts + ')');
        _reconnectTimer = setTimeout(async () => {
            _reconnectTimer = null;
            try {
                printLog('Attempting reconnect (attempt ' + _reconnectAttempts + ')');
                // re-join lobby/room on server to ensure server-side session exists
                await attemptServerJoin(currentRoom || 'lobby', {silent: true});
                // restart WebRTC session
                if (window.smartMicPC) {
                    try { window.smartMicPC.close(); } catch(e){}
                    window.smartMicPC = null;
                }
                await startWebRTCSession();
                _reconnectAttempts = 0; // reset on success
                printLog('Reconnect successful');
            } catch (e) {
                printLog('Reconnect failed: ' + e);
                // schedule next attempt
                tryScheduleReconnect();
            }
        }, delay * 1000);
    }

    // Try real-time updates via Server-Sent Events. If SSE is available on the
    // server, this will push immediate room updates; polling remains as a
    // fallback.
    try {
        if (typeof EventSource !== 'undefined') {
            const es = new EventSource('/rooms/stream');
            es.addEventListener('message', (ev) => {
                try {
                    const payload = JSON.parse(ev.data || '{}');
                    if (payload && payload.rooms) {
                        rooms = payload.rooms;
                        updateRoomDisplays();
                        const located = findRoomContainingSelf(payload.rooms);
                        if (located && located !== currentRoom) {
                            const now = Date.now();
                            if (now >= autoRejoinSuppressedUntil || located === desiredRoom) {
                                rememberCurrentRoom(located);
                            }
                        }
                        ensureRoomMembership('sse');
                        printLog('Received SSE rooms update');
                    }
                } catch (e) { /* ignore parse errors */ }
            });
            es.addEventListener('open', () => printLog('SSE connected to /rooms/stream'));
            es.addEventListener('error', (e) => { printLog('SSE error: ' + e); es.close(); });
        }
    } catch (e) { printLog('SSE not available: ' + e); }
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
        printLog('network error: ' + error.message)
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

// --- Control tab behavior ---
document.addEventListener('DOMContentLoaded', function() {
    const controlAcquireBtn = document.getElementById('controlAcquireBtn');
    const controlReleaseBtn = document.getElementById('controlReleaseBtn');
    const controlOwnerDiv = document.getElementById('controlOwner');
    const controlTextInput = document.getElementById('controlTextInput');
    const keyboardButtonsDiv = document.getElementById('keyboardButtons');

    let controlOwner = null;
    let controlName = null;

    function updateControlUI() {
        if (!controlOwner) {
            controlOwnerDiv.textContent = 'Control: free';
            controlAcquireBtn.style.display = 'inline-block';
            if (controlReleaseBtn) controlReleaseBtn.style.display = 'none';
            if (controlTextInput) controlTextInput.disabled = true;
        } else {
            controlOwnerDiv.textContent = `Control: ${controlName || controlOwner}`;
            controlAcquireBtn.style.display = 'none';
            if (controlReleaseBtn) controlReleaseBtn.style.display = 'inline-block';
            if (controlTextInput) controlTextInput.disabled = (localStorage.getItem('userName') !== controlName);
        }
    }

    async function fetchControlStatus() {
        try {
            const res = await fetch('/control/status');
            const data = await res.json();
            if (data) {
                controlOwner = data.owner;
                controlName = data.owner_name;
                updateControlUI();
            }
        } catch (e) {
            printLog('Failed to fetch control status: ' + e);
        }
    }

    // Acquire / release
    async function acquireControl() {
        try {
            const name = localStorage.getItem('userName') || '';
            const res = await fetch('/control/acquire', {method: 'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name: name}), credentials: 'include'});
            const data = await res.json();
            if (data && data.success) {
                controlOwner = data.owner;
                controlName = data.owner_name;
                updateControlUI();
                printLog('Acquired control');
            } else {
                printLog('Failed to acquire control: ' + (data && data.error));
            }
            return data;
        } catch (e) { printLog('Error acquiring control: '+e); }
    }

    async function releaseControl() {
        try {
            const res = await fetch('/control/release', {method: 'POST', credentials: 'include'});
            const data = await res.json();
            if (data && data.success) {
                controlOwner = null; controlName = null;
                updateControlUI();
                printLog('Released control');
            } else {
                printLog('Failed to release control: ' + (data && data.error));
            }
            return data;
        } catch (e) { printLog('Error releasing control: '+e); }
    }

    // Expose globally for tab switcher
    window.acquireControl = acquireControl;
    window.releaseControl = releaseControl;

    // Helper to ensure the control input is focused (useful for mobile soft keyboards)
    function focusControlInput() {
        try {
            if (controlTextInput) {
                controlTextInput.focus();
                try { controlTextInput.setSelectionRange(controlTextInput.value.length, controlTextInput.value.length); } catch (e) {}
            }
        } catch (e) {}
    }

    if (controlAcquireBtn) {
        controlAcquireBtn.addEventListener('click', async () => { await acquireControl(); focusControlInput(); });
    }
    if (controlReleaseBtn) {
        controlReleaseBtn.addEventListener('click', async () => { await releaseControl(); focusControlInput(); });
    }

    const searchBtn = document.getElementById('SearchBtn');
    if (searchBtn) {
        searchBtn.addEventListener('click', () => {
            try {
                if (controlTextInput) {
                    const ev = new KeyboardEvent('keydown', { key: 'F3', bubbles: true, cancelable: true });
                    controlTextInput.dispatchEvent(ev);
                }
            } catch (e) {
                // ignore dispatch failures
            }
            // refocus input so mobile keyboard stays open
            focusControlInput();
        });
    }

    // add eventlistener to button with id EscBtn
    const escBtn = document.getElementById('EscBtn');
    if (escBtn) {
        escBtn.addEventListener('click', () => {
            // Dispatch a synthetic keydown on the control input so the local handler
            // updates searchMode / escaped state exactly as if Escape was pressed.
            try {
                if (controlTextInput) {
                    const ev = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
                    controlTextInput.dispatchEvent(ev);
                }
            } catch (e) {
                // ignore dispatch failures
            }
            // refocus input so mobile keyboard stays open
            focusControlInput();
        });
    }

    // arrows (T layout)
    const arrowsWrap = document.createElement('div'); arrowsWrap.className = 'kbd-row';
    const arrowsGrid = document.createElement('div'); arrowsGrid.className = 'kbd-arrows';
    const empty = () => { const d = document.createElement('div'); d.className='empty'; return d; };
    arrowsGrid.appendChild(empty());
    const up = document.createElement('button'); up.className='kbd-btn k-up'; up.textContent='↑'; up.addEventListener('click', ()=>{ fetch('/control/keystroke',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({key:'ArrowUp'}), credentials:'include'}); focusControlInput(); });
    arrowsGrid.appendChild(up);
    arrowsGrid.appendChild(empty());
    const left = document.createElement('button'); left.className='kbd-btn k-left'; left.textContent='←'; left.addEventListener('click', ()=>{ fetch('/control/keystroke',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({key:'ArrowLeft'}), credentials:'include'}); focusControlInput(); });
    arrowsGrid.appendChild(left);
    const mid = document.createElement('div'); mid.className='empty'; arrowsGrid.appendChild(mid);
    const right = document.createElement('button'); right.className='kbd-btn k-right'; right.textContent='→'; right.addEventListener('click', ()=>{ fetch('/control/keystroke',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({key:'ArrowRight'}), credentials:'include'}); focusControlInput(); });
    arrowsGrid.appendChild(right);
    arrowsGrid.appendChild(empty());
    const down = document.createElement('button'); down.className='kbd-btn k-down'; down.textContent='↓'; down.addEventListener('click', ()=>{ fetch('/control/keystroke',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({key:'ArrowDown'}), credentials:'include'}); focusControlInput(); });
    arrowsGrid.appendChild(down);
    arrowsWrap.appendChild(arrowsGrid);
    keyboardButtonsDiv.appendChild(arrowsWrap);

    // Send keystrokes per keydown so the input remains responsive. Implement special "search mode" triggered by 'j'.
    if (controlTextInput) {
        let searchMode = false;    // true when we've entered the game's search/edit mode

        // initialize lastLength so we can detect deletes from mobile IME which sometimes report e.key="Process"
        controlTextInput.dataset.lastLength = String(controlTextInput.value ? controlTextInput.value.length : 0);
          controlTextInput.addEventListener('input', () => {
                  controlTextInput.dataset.lastLength = String(controlTextInput.value ? controlTextInput.value.length : 0);
          });

          controlTextInput.addEventListener('keydown', (e) => {
            // Handle entering search mode: when not in search mode and user types 'j',
            // send the 'j' key to the game (which opens search in-game) but prevent the
            // 'j' character from being inserted into the web input. Subsequent typing
            // while in searchMode should appear in the input and be sent to the game.
            let rawKey = (e.key || '').toString();
            let rawLower = rawKey.toLowerCase();

            if (rawLower == 'f3') {
              e.preventDefault();
              if (!searchMode) {
                rawKey = 'J';
                rawLower = 'j';
              }
              else {
                return;
              }
            }
            

            // check if last character in content was removed, if so, rawKey/rawLower was backspace although the keyboard did not send it as such
            if (searchMode && rawLower === 'process' || e.keyCode === 229) {
                rawKey = 'backspace';
                rawLower = 'backspace';
            }

            if (!searchMode && rawLower === 'j') {
                // send 'j' to the game, but prevent it from appearing in the input
                e.preventDefault();
                const k = 'j';
                fetch('/control/keystroke', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({key: k}), credentials:'include'})
                    .then(r=>r.json()).then(d=>{ if (!d || !d.success) printLog('Keystroke failed: ' + (d && d.error)); })
                    .catch(err=>printLog('Keystroke network error: '+err));
                searchMode = true;
                // focus and keep input as-is for typing search text
                return;
            }

            // While in search mode, Enter or Escape have special behavior
            if (searchMode) {
                if (rawLower === 'enter' || rawLower === 'return') {
                    // send Enter to game and exit search mode (keep content)
                    e.preventDefault();
                    fetch('/control/keystroke', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({key: 'Enter'}), credentials:'include'})
                        .then(r=>r.json()).then(d=>{ if (!d || !d.success) printLog('Keystroke failed: ' + (d && d.error)); })
                        .catch(err=>printLog('Keystroke network error: '+err));
                    searchMode = false;
                    return;
                }
                if (rawLower === 'escape' || rawLower === 'esc') {
                    // first Escape: exit search mode but keep typed content
                    e.preventDefault();
                    fetch('/control/keystroke', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({key: 'Escape'}), credentials:'include'})
                        .then(r=>r.json()).then(d=>{ if (!d || !d.success) printLog('Keystroke failed: ' + (d && d.error)); })
                        .catch(err=>printLog('Keystroke network error: '+err));
                    searchMode = false;
                    return;
                }
                // Otherwise allow printable chars to appear and send them as keystrokes below.
            }

            // Default per-key sending: printable characters and a few control keys
            const allowed = ['Enter','Backspace','Escape','ArrowLeft','ArrowRight','ArrowUp','ArrowDown'];
            // Normalize mobile variants like 'backspace' or 'delete'
            let keyToSend = null;
            if (rawKey === ' ') {
                keyToSend = 'Space';
            } else if (rawKey.length === 1) {
                keyToSend = rawKey;
            } else if (rawLower === 'backspace' || rawLower === 'delete') {
                keyToSend = 'Backspace';
            } else if (rawLower === 'enter' || rawLower === 'return') {
                keyToSend = 'Enter';
            } else if (rawLower === 'escape' || rawLower === 'esc') {
                keyToSend = 'Escape';
            } else if (rawLower.startsWith('arrow')) {
                // keep ArrowLeft/ArrowRight etc
                keyToSend = rawKey;
            }

            if ((searchMode == true && keyToSend === 'Backspace') || keyToSend != 'Backspace') {
                fetch('/control/keystroke', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({key: keyToSend}), credentials:'include'})
                    .then(r=>r.json()).then(d=>{ if (!d || !d.success) printLog('Keystroke failed: ' + (d && d.error)); })
                    .catch(err=>printLog('Keystroke network error: '+err));
            }
        });

        // If a paste occurs (multiple chars inserted), send the full text once.
        controlTextInput.addEventListener('paste', (ev) => {
            // let the paste complete and then send text
            setTimeout(async () => {
                const txt = controlTextInput.value || '';
                try {
                    const res = await fetch('/control/text', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({text: txt}), credentials:'include'});
                    const data = await res.json();
                    if (!data || !data.success) printLog('Send text failed: ' + (data && data.error));
                } catch (err) { printLog('Send text error: '+err); }
            }, 50);
        });
    }

    // Poll control status every 2s
    fetchControlStatus();
    setInterval(fetchControlStatus, 2000);

    // Release control on unload if we are the owner
    window.addEventListener('beforeunload', () => {
        try { window.releaseControl && window.releaseControl(); } catch(e){}
    });

});

// Bottom video behavior: attach provided base64 mp4 and make click request fullscreen
document.addEventListener('DOMContentLoaded', function() {
    try {
        const video = document.getElementById('lockVideo');
        if (!video) return;

    // Attempt autoplay to prime playback; ignore rejections due to autoplay policies
    try { video.muted = true; video.loop = true; video.playsInline = true; try { video.play().catch(()=>{}); } catch(e){} } catch (e) {}

        // clicking the video requests fullscreen for the video element
        video.addEventListener('click', async (ev) => {
            ev.stopPropagation();
            try {
                if (document.fullscreenElement === video) {
                    return;
                }
            } catch (e) {}
            const req = video.requestFullscreen || video.webkitRequestFullscreen || video.mozRequestFullScreen || video.msRequestFullscreen;
            if (req) {
                try { req.call(video); return; } catch (e) { console.warn('requestFullscreen failed', e); }
            }
            if (video.webkitEnterFullscreen) {
                try { video.webkitEnterFullscreen(); return; } catch (e) { console.warn('webkitEnterFullscreen failed', e); }
            }
            try { await document.documentElement.requestFullscreen(); } catch (e) { console.warn('document.requestFullscreen fallback failed', e); }
        }, { passive: true });

    } catch (e) {
        console.error('Lock video init failed:', e);
    }
});