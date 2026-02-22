// Debug output field (initialized on DOMContentLoaded)
let debugOutputField = null;
const CONTROL_ONLY_MODE = (() => {
    try {
        const body = document.body || document.documentElement;
        const flag = body && body.dataset ? body.dataset.controlOnly : null;
        return (flag || '').toLowerCase() === 'true';
    } catch (e) {
        return false;
    }
})();

const MAX_NAME_LENGTH = (() => {
    try {
        const body = document.body || document.documentElement;
        const val = body && body.dataset ? parseInt(body.dataset.maxNameLength || '16', 10) : 16;
        return Number.isFinite(val) && val > 0 ? val : 16;
    } catch (e) {
        return 16;
    }
})();
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
    // Tab buttons are fully managed by the later DOMContentLoaded handler that
    // shows/hides the main panels. Keep this block focused on debug/init only.
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
    if (tabControl) tabControl.addEventListener('click', async (ev) => {
        ev.preventDefault();
        const ok = await promptForControlPasswordIfNeeded();
        if (ok) {
            showTab(tabControl);
        } else if (tabMic) {
            showTab(tabMic);
        }
    });
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

const MIC_ROOM_KEYS = ['mic1', 'mic2', 'mic3', 'mic4', 'mic5', 'mic6'];
const DEFAULT_ROOM_CAP_LIMIT = 6;

let lockVideoEl = null;
let lockLabelEl = null;

function ensureLockScreenElements() {
    if (!lockVideoEl || !lockVideoEl.isConnected) {
        lockVideoEl = document.getElementById('lockVideo');
    }
    if (!lockLabelEl || !lockLabelEl.isConnected) {
        lockLabelEl = document.getElementById('lockLabel');
    }
}

function describeRoomForLockLabel(roomKey) {
    if (!roomKey) return '';
    if (roomKey.startsWith('mic')) {
        const num = roomKey.replace(/[^0-9]/g, '');
        return num ? `Mic ${num}` : roomKey.toUpperCase();
    }
    return roomKey.charAt(0).toUpperCase() + roomKey.slice(1);
}

function hideLockScreenVideo() {
    ensureLockScreenElements();
    if (!lockVideoEl) return;
    try { lockVideoEl.pause(); } catch (e) {}
    try {
        if (lockVideoEl.getAttribute('src')) {
            lockVideoEl.removeAttribute('src');
            lockVideoEl.load();
        }
    } catch (e) {}
    if (lockVideoEl.dataset) {
        delete lockVideoEl.dataset.currentVideo;
    }
    lockVideoEl.style.display = 'none';
    if (lockLabelEl) {
        lockLabelEl.style.display = 'none';
    }
}

// Enhanced: allow dynamic video selection for mic level bar (3-level, 5s min interval, max peak, user setting)
let _lastMicLevelVideoKey = null;
let _lastMicLevelVideoTime = 0;
let _maxMicPeak = 0;
function updateLockScreenVideo(roomName, opts = {}) {
    ensureLockScreenElements();
    if (!lockVideoEl) return;
    // Determine if mic bar is enabled
    let micBarEnabled = true;
    try {
        micBarEnabled = localStorage.getItem('optLockScreenMicBar') !== 'false';
    } catch (e) {}
    // If opts.micLevel is set, use it to pick video (simulate mic bar)
    let key = (roomName || '').toLowerCase();
    let variant = 3; // default: always show -3 if disabled
    if (opts.micLevel != null && micBarEnabled) {
        // Track max peak so far (reset on reload)
        _maxMicPeak = Math.max(_maxMicPeak, opts.micLevel);
        // Use max peak for scaling
        let scaled = _maxMicPeak > 0 ? opts.micLevel / _maxMicPeak : 0;
        // 3 levels: 1 (low), 2 (med), 3 (high)
        if (scaled < 0.33) variant = 1;
        else if (scaled < 0.66) variant = 2;
        else variant = 3;
    }
    // Always use -3 if disabled
    if (!micBarEnabled) {
        variant = 3;
    }
    // Always use the correct P1–P6 prefix for the current room/player
    // If roomName is not provided, fallback to currentRoom
    let roomKey = key;
    if (!roomKey) {
        try {
            roomKey = (typeof currentRoom === 'string' ? currentRoom : 'mic1');
        } catch (e) { roomKey = 'mic1'; }
    }
    let prefix = roomKey.replace('mic', 'P');
    let videoFile = `/static/${prefix}-${variant}.mp4`;
    // Only update if video changed and at least 5s since last change
    const now = Date.now();
    if (_lastMicLevelVideoKey !== videoFile && (now - _lastMicLevelVideoTime > 5000 || _lastMicLevelVideoKey === null)) {
        if (lockVideoEl.dataset) {
            lockVideoEl.dataset.currentVideo = videoFile;
        }
        lockVideoEl.src = videoFile;
        try { lockVideoEl.load(); } catch (e) {}
        _lastMicLevelVideoKey = videoFile;
        _lastMicLevelVideoTime = now;
    }
    lockVideoEl.style.display = 'block';
    if (lockLabelEl) {
        lockLabelEl.style.display = 'flex';
        const label = describeRoomForLockLabel(key);
        lockLabelEl.textContent = label ? `Lock Screen — ${label}` : 'Lock Screen';
    }
    try { lockVideoEl.play().catch(() => {}); } catch (e) {}
}

window.controlPasswordState = window.controlPasswordState || {required: false, verified: false};

function updateControlPasswordState(payload) {
    if (!payload) return window.controlPasswordState;
    const required = !!payload.password_required;
    const verified = required ? !!payload.password_ok : true;
    const state = window.controlPasswordState;
    state.required = required;
    state.verified = verified;
    return state;
}

async function promptForControlPasswordIfNeeded(message) {
    const state = window.controlPasswordState || {};
    if (!state.required) return true;
    if (state.verified) return true;
    const promptMessage = message || 'Enter the control password:';
    const entered = window.prompt(promptMessage);
    if (entered === null) {
        return false;
    }
    try {
        const res = await fetch('/control/auth', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            credentials: 'include',
            body: JSON.stringify({password: entered})
        });
        const data = await res.json();
        if (data && data.success) {
            updateControlPasswordState({password_required: true, password_ok: true});
            return true;
        }
        const errMsg = (data && data.error) ? data.error : 'Invalid control password.';
        alert(errMsg);
    } catch (e) {
        alert('Unable to verify control password: ' + e);
    }
    updateControlPasswordState({password_required: true, password_ok: false});
    return false;
}

window.updateControlPasswordState = updateControlPasswordState;
window.promptForControlPasswordIfNeeded = promptForControlPasswordIfNeeded;


let wakeLockHandle = null;
let wakeLockEnabled = false;
let wakeLockVisibilityBound = false;
let latencyByName = {};
let lastStatusRttMs = null;
let lastServerAudioSeenMs = null;
let lastMetricsSentAt = 0;
let serverAudioWarningAt = 0;

function getSilenceInterventionMs() {
    let seconds = 5;
    try {
        const stored = parseFloat(localStorage.getItem('optSilenceThresholdSeconds') || '5');
        if (Number.isFinite(stored) && stored > 0) seconds = stored;
    } catch (e) {}
    return Math.max(0.3, Math.min(10, seconds)) * 1000;
}

function connectionNotificationsEnabled() {
    try {
        return localStorage.getItem('optConnectionNotifications') === 'true';
    } catch (e) {
        return false;
    }
}

async function ensureNotificationPermission() {
    if (!('Notification' in window)) return false;
    if (Notification.permission === 'granted') return true;
    if (Notification.permission === 'denied') return false;
    try {
        const res = await Notification.requestPermission();
        return res === 'granted';
    } catch (e) {
        return false;
    }
}

async function sendConnectionNotification(message) {
    if (!connectionNotificationsEnabled()) return;
    if (!('Notification' in window)) return;
    const ok = await ensureNotificationPermission();
    if (!ok) return;
    try {
        new Notification('Connection unstable', {body: message || 'Attempting to recover the microphone connection.'});
    } catch (e) {
        // ignore notification failures
    }
}

async function applyWakeLockSetting(enabled) {
    wakeLockEnabled = !!enabled;
    if (!('wakeLock' in navigator)) return;
    try {
        if (!wakeLockEnabled) {
            if (wakeLockHandle) {
                await wakeLockHandle.release();
                wakeLockHandle = null;
            }
            return;
        }
        if (!wakeLockHandle) {
            wakeLockHandle = await navigator.wakeLock.request('screen');
            wakeLockHandle.addEventListener('release', () => {
                wakeLockHandle = null;
            });
        }
    } catch (e) {
        wakeLockHandle = null;
    }
}

function bindWakeLockVisibilityHandler() {
    if (wakeLockVisibilityBound) return;
    wakeLockVisibilityBound = true;
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
            if (wakeLockEnabled) {
                applyWakeLockSetting(true);
            }
        } else {
            if (wakeLockHandle) {
                try { wakeLockHandle.release(); } catch (e) {}
                wakeLockHandle = null;
            }
        }
    });
}

const micHealth = {
    ui: {
        card: null,
        levelFill: null,
        levelValue: null,
        message: null,
        reloadBtn: null
    },
    audioContext: null,
    analyser: null,
    dataArray: null,
    raf: null,
    source: null,
    stream: null,
    silenceStart: null,
    warningShown: false,
    reloadPromptShown: false,
    lastInterventionAt: 0,
    interventionInFlight: false,
    lastLevel: 0,
    lastLocalAudioAt: null
};

function initMicHealthUI() {
    if (micHealth.ui.card) return;
    micHealth.ui.card = document.getElementById('micHealthCard');
    micHealth.ui.levelFill = document.getElementById('micLevelFill');
    micHealth.ui.levelValue = document.getElementById('micLevelValue');
    micHealth.ui.message = document.getElementById('micStatusMessage');
    micHealth.ui.reloadBtn = document.getElementById('micReloadBtn');
    if (micHealth.ui.reloadBtn && !micHealth.ui.reloadBtn.dataset.bound) {
        micHealth.ui.reloadBtn.dataset.bound = 'true';
        micHealth.ui.reloadBtn.addEventListener('click', () => {
            try { window.location.reload(); } catch (e) { location.href = location.href; }
        });
    }
}

function setMicStatusMessage(text, options = {}) {
    initMicHealthUI();
    const ui = micHealth.ui;
    if (ui.message) {
        ui.message.textContent = text || '';
    }
    if (ui.card) {
        ui.card.dataset.severity = options.severity || 'info';
    }
    if (ui.reloadBtn) {
        ui.reloadBtn.style.display = options.showReload ? 'inline-flex' : 'none';
    }
}

function showMicReloadPrompt(reason, options = {}) {
    if (options.sticky) {
        micHealth.reloadPromptShown = true;
    }
    setMicStatusMessage(reason || 'Please reload this page to activate your microphone completely.', {
        severity: 'warn',
        showReload: true
    });
}

function applyControlOnlyModeUI() {
    try { initMicHealthUI(); } catch (e) {}
    const card = micHealth.ui.card || document.getElementById('micHealthCard');
    if (card) {
        card.style.display = 'none';
    }
    const audioOptions = document.getElementById('audioOptionsSection');
    if (audioOptions) {
        audioOptions.style.display = 'none';
    }
}

function stopMicMeter() {
    if (micHealth.raf) {
        cancelAnimationFrame(micHealth.raf);
        micHealth.raf = null;
    }
    if (micHealth.source) {
        try { micHealth.source.disconnect(); } catch (e) {}
        micHealth.source = null;
    }
    micHealth.stream = null;
    micHealth.silenceStart = null;
    micHealth.warningShown = false;
    if (micHealth.ui.levelFill) {
        micHealth.ui.levelFill.style.transform = 'scaleX(0)';
    }
    if (micHealth.ui.levelValue) {
        micHealth.ui.levelValue.textContent = '--';
    }
}

function startMicLevelLoop() {
    if (!micHealth.analyser) return;
    if (!micHealth.dataArray || micHealth.dataArray.length !== micHealth.analyser.fftSize) {
        micHealth.dataArray = new Uint8Array(micHealth.analyser.fftSize);
    }
    // Reset max peak on reload
    _maxMicPeak = 0;
    const step = () => {
        micHealth.analyser.getByteTimeDomainData(micHealth.dataArray);
        let sum = 0;
        for (let i = 0; i < micHealth.dataArray.length; i++) {
            const v = (micHealth.dataArray[i] - 128) / 128;
            sum += v * v;
        }
            const rms = Math.sqrt(sum / micHealth.dataArray.length);
        const level = Math.min(1, rms * 2);
        micHealth.lastLevel = level;
        if (micHealth.ui.levelFill) {
            micHealth.ui.levelFill.style.transform = `scaleX(${level})`;
            micHealth.ui.levelFill.style.background = level > 0.6 ? '#22c55e' : (level > 0.3 ? '#facc15' : '#f97316');
        }
        if (micHealth.ui.levelValue) {
            micHealth.ui.levelValue.textContent = `${Math.round(level * 100)}%`;
        }
        // If lock screen video is visible, update video to match mic level (with 5s min interval, 3-level, max peak, user setting)
        if (lockVideoEl && lockVideoEl.style.display !== 'none') {
            updateLockScreenVideo(null, {micLevel: level});
        }
        const now = performance.now();
        const silenceThreshold = 0.02;
        const localActiveThreshold = 0.01;
        if (level >= localActiveThreshold) {
            micHealth.lastLocalAudioAt = now;
        }
        if (level < silenceThreshold) {
            if (!micHealth.silenceStart) {
                micHealth.silenceStart = now;
            } else {
                const silenceMs = now - micHealth.silenceStart;
                const interventionMs = getSilenceInterventionMs();
                if (silenceMs > interventionMs && !micHealth.interventionInFlight) {
                    const cooldown = 15000;
                    if (now - micHealth.lastInterventionAt > cooldown) {
                        micHealth.lastInterventionAt = now;
                        micHealth.interventionInFlight = true;
                        setMicStatusMessage('Microphone is quiet. If you are speaking, check mic access.', {severity: 'warn'});
                        setTimeout(() => { micHealth.interventionInFlight = false; }, 4000);
                    }
                }
                if (!micHealth.warningShown && silenceMs > interventionMs * 3) {
                    micHealth.warningShown = true;
                    showMicReloadPrompt('We are not receiving audio from your mic. Please reload to wake it up.');
                }
            }
        } else {
            micHealth.silenceStart = null;
            if (!micHealth.reloadPromptShown) {
                setMicStatusMessage('Microphone is sending audio.', {severity: 'ok', showReload: false});
            }
            micHealth.warningShown = false;
        }
        micHealth.raf = requestAnimationFrame(step);
    };
    if (micHealth.raf) cancelAnimationFrame(micHealth.raf);
    micHealth.raf = requestAnimationFrame(step);
}

function attachMicStreamToMeter(stream) {
    try { initMicHealthUI(); } catch (e) {}
    micHealth.stream = stream;
    micHealth.warningShown = false;
    micHealth.silenceStart = null;
    if (!micHealth.audioContext) {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (!AudioCtx) {
            setMicStatusMessage('Audio meter unsupported in this browser.', {severity: 'info'});
            return;
        }
        micHealth.audioContext = new AudioCtx();
    }
    try { micHealth.audioContext.resume(); } catch (e) {}
    if (micHealth.source) {
        try { micHealth.source.disconnect(); } catch (e) {}
    }
    micHealth.source = micHealth.audioContext.createMediaStreamSource(stream);
    micHealth.analyser = micHealth.audioContext.createAnalyser();
    micHealth.analyser.fftSize = 2048;
    micHealth.source.connect(micHealth.analyser);
    setMicStatusMessage('Listening for audio…', {severity: 'info'});
    startMicLevelLoop();
}

async function sendClientMetrics() {
    if (CONTROL_ONLY_MODE) return;
    if (!micHealth.stream) return;
    const now = Date.now();
    if (now - lastMetricsSentAt < 900) return;
    lastMetricsSentAt = now;
    const payload = {
        latency_ms: lastStatusRttMs,
        audio_level: micHealth.lastLevel
    };
    try {
        await fetch('/client/metrics', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            credentials: 'include',
            body: JSON.stringify(payload)
        });
    } catch (e) {
        // ignore metrics errors
    }
}

function handleFirstPermissionReloadHint() {
    if (micHealth.reloadPromptShown) return;
    try {
        const key = 'micPermissionEverGranted';
        const seen = localStorage.getItem(key) === 'true';
        if (!seen) {
            localStorage.setItem(key, 'true');
            showMicReloadPrompt('Mic permission granted! Reload once to make the audio path stable.', {sticky: true});
        }
    } catch (e) {
        showMicReloadPrompt('Mic permission granted! Reload once to make the audio path stable.', {sticky: true});
    }
}

document.addEventListener('DOMContentLoaded', function() {
    let userName = localStorage.getItem('userName') || "";
    if (typeof userName === 'string' && userName.length > MAX_NAME_LENGTH) {
        userName = userName.slice(0, MAX_NAME_LENGTH);
        try { localStorage.setItem('userName', userName); } catch (e) {}
    }
    const nameEntry = document.getElementById('nameEntry');
    const mainLobby = document.getElementById('mainLobby');
    const userNameInput = document.getElementById('userNameInput');
    const saveNameBtn = document.getElementById('saveNameBtn');
    const micBoxes = Array.from(document.querySelectorAll('.micBox'));
    const lobbyNamesDiv = document.getElementById('lobbyNames');
    const roomMessageBanner = document.getElementById('roomMessage');
    const capacitySlider = document.getElementById('capacitySlider');
    const capacityValueLabel = document.getElementById('capacityValue');
    const capacityEditStatus = document.getElementById('capacityEditStatus');
    let capacityEditingEnabled = false;
    let roomCapacity = {};
    MIC_ROOM_KEYS.forEach(key => { roomCapacity[key] = DEFAULT_ROOM_CAP_LIMIT; });

    function applyCapacityEditState(enabled, opts = {}) {
        const nextState = !!enabled;
        if (!opts.force && capacityEditingEnabled === nextState) return;
        capacityEditingEnabled = nextState;
        if (capacitySlider) {
            capacitySlider.disabled = !capacityEditingEnabled;
        }
        if (capacityEditStatus) {
            capacityEditStatus.textContent = capacityEditingEnabled
                ? 'You currently control the screen and can change channel limits.'
                : 'Acquire control to change channel limits.';
        }
    }

    window.setCapacityControlEditable = function(enabled) {
        applyCapacityEditState(enabled, {force: true});
    };

    function clampCapacityValue(val) {
        let num = Number(val);
        if (!Number.isFinite(num)) num = DEFAULT_ROOM_CAP_LIMIT;
        return Math.min(6, Math.max(1, Math.round(num)));
    }

    function formatCapacityLabel(val) {
        const num = clampCapacityValue(val);
        return `${num} ${num === 1 ? 'singer' : 'singers'}`;
    }

    function prettyRoomName(room) {
        if (!room) return 'Room';
        if (room === 'lobby') return 'Lobby';
        const idx = MIC_ROOM_KEYS.indexOf(room);
        return idx >= 0 ? `Mic ${idx + 1}` : room;
    }

    function escapeHtml(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function renderNameLabel(name, isSelf) {
        const safeName = escapeHtml(name);
        const latency = latencyByName && Object.prototype.hasOwnProperty.call(latencyByName, name)
            ? latencyByName[name]
            : null;
        const latencyLabel = Number.isFinite(latency) ? `<span style="font-size:0.4em"> ${latency}ms</span>` : '';
        return isSelf ? `<strong>${safeName}</strong>${latencyLabel}` : `${safeName}${latencyLabel}`;
    }

    function showRoomMessage(text, options = {}) {
        if (!roomMessageBanner) return;
        if (!text) {
            roomMessageBanner.style.display = 'none';
            return;
        }
        roomMessageBanner.textContent = text;
        roomMessageBanner.dataset.severity = options.severity || 'info';
        roomMessageBanner.style.display = 'block';
    }

    function getUnifiedCapacityValue() {
        let found = null;
        for (const room of MIC_ROOM_KEYS) {
            const limit = roomCapacity[room];
            if (Number.isFinite(limit)) {
                found = clampCapacityValue(limit);
                break;
            }
        }
        return found ?? DEFAULT_ROOM_CAP_LIMIT;
    }

    function syncCapacitySlider() {
        if (!capacitySlider) return;
        const value = String(getUnifiedCapacityValue());
        if (capacitySlider.value !== value) {
            capacitySlider.value = value;
        }
        if (capacityValueLabel) {
            capacityValueLabel.textContent = formatCapacityLabel(value);
        }
    }

    function setRoomCapacityState(nextCaps, opts = {}) {
        if (!nextCaps) return;
        let changed = false;
        MIC_ROOM_KEYS.forEach(room => {
            if (nextCaps[room] == null) return;
            const normalized = clampCapacityValue(nextCaps[room]);
            if (roomCapacity[room] !== normalized) {
                roomCapacity[room] = normalized;
                changed = true;
            }
        });
        if (changed || opts.forceSync) {
            syncCapacitySlider();
            try { updateRoomDisplays(); } catch (e) {}
        }
    }

    async function sendCapacityUpdate(limit) {
        if (capacitySlider) capacitySlider.disabled = true;
        if (!capacityEditingEnabled) {
            showRoomMessage('Acquire control to change channel limits.', {severity: 'warn'});
            syncCapacitySlider();
            if (capacitySlider) capacitySlider.disabled = true;
            return;
        }
        const normalized = clampCapacityValue(limit);
        const payload = {capacity: {}};
        MIC_ROOM_KEYS.forEach(room => {
            payload.capacity[room] = normalized;
        });
        try {
            const res = await fetch('/rooms/capacity', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                credentials: 'include',
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            if (data && data.success && data.capacity) {
                setRoomCapacityState(data.capacity, {forceSync: true});
                showRoomMessage(`All mic rooms now allow ${formatCapacityLabel(normalized)}.`, {severity: 'info'});
            } else {
                const errMsg = (data && data.error) ? data.error : 'Unknown capacity error';
                showRoomMessage(errMsg, {severity: 'warn'});
                throw new Error(errMsg);
            }
        } catch (e) {
            printLog('Failed to update capacity: ' + e);
            syncCapacitySlider();
        } finally {
            if (capacitySlider) capacitySlider.disabled = !capacityEditingEnabled;
        }
    }

    function initCapacitySlider() {
        if (!capacitySlider || capacitySlider.dataset.ready === 'true') return;
        capacitySlider.value = String(getUnifiedCapacityValue());
        capacitySlider.disabled = !capacityEditingEnabled;
        if (capacityValueLabel) {
            capacityValueLabel.textContent = formatCapacityLabel(capacitySlider.value);
        }
        capacitySlider.addEventListener('input', () => {
            if (capacityValueLabel) {
                capacityValueLabel.textContent = formatCapacityLabel(capacitySlider.value);
            }
        });
        capacitySlider.addEventListener('change', () => {
            const normalized = clampCapacityValue(capacitySlider.value);
            capacitySlider.value = String(normalized);
            if (capacityValueLabel) {
                capacityValueLabel.textContent = formatCapacityLabel(normalized);
            }
            sendCapacityUpdate(normalized);
        });
        capacitySlider.dataset.ready = 'true';
        applyCapacityEditState(capacityEditingEnabled, {force: true});
        syncCapacitySlider();
    }

    initCapacitySlider();
    applyCapacityEditState(false, {force: true});
    showRoomMessage('Tap a mic to join a channel.');
    initMicHealthUI();
    setMicStatusMessage('Waiting for microphone permission…', {severity: 'info'});
    if (CONTROL_ONLY_MODE) {
        applyControlOnlyModeUI();
    }

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
        updateLockScreenVideo(currentRoom);
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
            return {success: false, reason: 'no_name'};
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
                if (data.capacity) {
                    setRoomCapacityState(data.capacity, {forceSync: true});
                }
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
                if (!silent) {
                    const limit = roomCapacity[serverRoom] ?? DEFAULT_ROOM_CAP_LIMIT;
                    const count = Array.isArray(rooms[serverRoom]) ? rooms[serverRoom].length : 0;
                    showRoomMessage(`Joined ${prettyRoomName(serverRoom)} (${count}/${limit}).`, {severity: 'ok'});
                }
                return {success: true};
            } else {
                if (data && data.capacity_map) {
                    setRoomCapacityState(data.capacity_map, {forceSync: true});
                }
                if (data && data.rooms) {
                    rooms = data.rooms;
                    updateRoomDisplays();
                }
                let failureReason = 'server_error';
                if (!silent) {
                    if (data && data.error_code === 'room_full') {
                        const limit = data.capacity || roomCapacity[roomName] || DEFAULT_ROOM_CAP_LIMIT;
                        const count = data.members ?? (Array.isArray(rooms[roomName]) ? rooms[roomName].length : 0);
                        const message = `${prettyRoomName(roomName)} is full (${count}/${limit}).`;
                        showRoomMessage(message, {severity: 'warn'});
                        printLog(message);
                        failureReason = 'room_full';
                    } else {
                        const errMsg = data && data.error ? data.error : 'Server join failed';
                        showRoomMessage(errMsg, {severity: 'warn'});
                        printLog(errMsg);
                    }
                }
                return {success: false, reason: failureReason};
            }
        } catch (e) {
            if (!silent) {
                printLog('Server join error: ' + e);
                showRoomMessage('Unable to reach server: ' + e, {severity: 'warn'});
            }
            return {success: false, reason: 'network', error: e};
        }
        return {success: false, reason: 'unknown'};
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
        if (!CONTROL_ONLY_MODE) {
            try {
                if (!window.smartMicPC) {
                    startWebRTCSession().catch(e => printLog('WebRTC start failed: ' + e));
                }
            } catch (e) {
                printLog('Error starting WebRTC: ' + e);
            }
        } else {
            printLog('Control-only mode active: skipping WebRTC microphone startup.');
            applyControlOnlyModeUI();
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
            if (val.length > MAX_NAME_LENGTH) {
                val = val.slice(0, MAX_NAME_LENGTH);
                userNameInput.value = val;
            }
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
    if (CONTROL_ONLY_MODE) {
        const audioOptions = document.getElementById('audioOptionsSection');
        if (audioOptions) audioOptions.style.display = 'none';
    } else {
        try {
            // AGC default: true for new users
            if (localStorage.getItem('optAutoGain') === null) {
                localStorage.setItem('optAutoGain', 'true');
            }
            const ns = localStorage.getItem('optNoiseSuppression') === 'true';
            const ec = localStorage.getItem('optEchoCancellation') === 'true';
            const ag = localStorage.getItem('optAutoGain') === 'true';
            const nsEl = document.getElementById('optNoiseSuppression');
            const ecEl = document.getElementById('optEchoCancellation');
            const agEl = document.getElementById('optAutoGain');
            if (nsEl) { nsEl.checked = ns; nsEl.addEventListener('change', () => { localStorage.setItem('optNoiseSuppression', nsEl.checked); }); }
            if (ecEl) { ecEl.checked = ec; ecEl.addEventListener('change', () => { localStorage.setItem('optEchoCancellation', ecEl.checked); }); }
            if (agEl) { agEl.checked = ag; agEl.addEventListener('change', () => { localStorage.setItem('optAutoGain', agEl.checked); }); }

            // Lock screen mic bar default: true for new users
            if (localStorage.getItem('optLockScreenMicBar') === null) {
                localStorage.setItem('optLockScreenMicBar', 'true');
            }
            const micBar = localStorage.getItem('optLockScreenMicBar') === 'true';
            const micBarEl = document.getElementById('optLockScreenMicBar');
            if (micBarEl) {
                micBarEl.checked = micBar;
                micBarEl.addEventListener('change', () => {
                    localStorage.setItem('optLockScreenMicBar', micBarEl.checked);
                });
            }

            if (localStorage.getItem('optConnectionNotifications') === null) {
                localStorage.setItem('optConnectionNotifications', 'true');
            }
            const notifyEl = document.getElementById('optConnectionNotifications');
            if (notifyEl) {
                notifyEl.checked = localStorage.getItem('optConnectionNotifications') === 'true';
                notifyEl.addEventListener('change', async () => {
                    localStorage.setItem('optConnectionNotifications', notifyEl.checked);
                    if (notifyEl.checked) {
                        await ensureNotificationPermission();
                    }
                });
            }

            if (localStorage.getItem('optWakeLock') === null) {
                localStorage.setItem('optWakeLock', 'true');
            }
            const wakeEl = document.getElementById('optWakeLock');
            if (wakeEl) {
                wakeEl.checked = localStorage.getItem('optWakeLock') === 'true';
                wakeEl.addEventListener('change', () => {
                    localStorage.setItem('optWakeLock', wakeEl.checked);
                    applyWakeLockSetting(wakeEl.checked);
                });
                bindWakeLockVisibilityHandler();
                applyWakeLockSetting(wakeEl.checked);
            }

            const silenceInput = document.getElementById('optSilenceThreshold');
            if (silenceInput) {
                const current = getSilenceInterventionMs() / 1000;
                silenceInput.value = String(current);
                silenceInput.addEventListener('change', () => {
                    let val = parseFloat(silenceInput.value);
                    if (!Number.isFinite(val) || val <= 0) {
                        val = 5;
                    }
                    val = Math.max(0.3, Math.min(10, val));
                    silenceInput.value = String(val);
                    localStorage.setItem('optSilenceThresholdSeconds', String(val));
                });
            }
        } catch (e) { }
    }
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
    const joinRes = await attemptServerJoin('lobby', {silent});
    if (joinRes && joinRes.success) return;

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
    const joinRes = await attemptServerJoin(target, {silent});
    if (joinRes && joinRes.success) return;

        // Fallback to local-only behavior if server not available
        Object.keys(rooms).forEach(room => { rooms[room] = rooms[room].filter(u => u !== userName); });
        rooms[target].push(userName);
        rememberCurrentRoom(target);
        updateRoomDisplays();
    }

    window.triggerConnectionIntervention = async function(reason) {
        if (!userName) return;
        const prevRoom = desiredRoom || currentRoom || 'lobby';
        if (prevRoom === 'lobby') return;
        if (Date.now() < autoRejoinSuppressedUntil) return;
        showRoomMessage('Connection unstable — reconnecting…', {severity: 'warn'});
        try {
            await attemptServerJoin('lobby', {silent: true, suppressMs: 2000});
            await new Promise(resolve => setTimeout(resolve, 900));
            await attemptServerJoin(prevRoom, {silent: true, suppressMs: 2000});
        } catch (e) {
            printLog('Intervention reconnect failed: ' + e);
        }
    };

    function updateRoomDisplays() {
    //printLog(`Room state updated: ${JSON.stringify(rooms)}`);
        // Update lobby
        lobbyNamesDiv.innerHTML = '';
        if (rooms.lobby.length === 0) {
            lobbyNamesDiv.textContent = '(No users in lobby)';
        } else {
            rooms.lobby.forEach(name => {
                let span = document.createElement('span');
                span.innerHTML = renderNameLabel(name, name === userName);
                lobbyNamesDiv.appendChild(span);
            });
        }
        // Update mic boxes
        micBoxes.forEach((box, idx) => {
            let roomName = 'mic' + (idx + 1);
            let userSpan = box.querySelector('.micUser');
            const labelEl = box.querySelector('.micLabel');
            const currentMembers = Array.isArray(rooms[roomName]) ? rooms[roomName] : [];
            const limit = roomCapacity[roomName] ?? DEFAULT_ROOM_CAP_LIMIT;
            if (labelEl) {
                labelEl.textContent = `Mic ${idx + 1} (${currentMembers.length}/${limit})`;
            }
            if (currentMembers.length > 0) {
                box.classList.add('occupied');
                userSpan.innerHTML = currentMembers.map(n => renderNameLabel(n, n === userName)).join(', ');
            } else {
                box.classList.remove('occupied');
                userSpan.innerHTML = '';
            }
        });
        try { window.roomsState = JSON.parse(JSON.stringify(rooms)); } catch (e) { window.roomsState = rooms; }
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
            const startedAt = performance.now();
            const res = await fetch('/status', {credentials: 'include'});
            const data = await res.json();
            lastStatusRttMs = Math.round(performance.now() - startedAt);
            if (data && data.success) {
                if (data.rooms) {
                    rooms = data.rooms;
                    updateRoomDisplays();
                    ensureRoomMembership('poll');
                }
                if (data.capacity) {
                    setRoomCapacityState(data.capacity);
                }
                if (data.latency_by_name) {
                    latencyByName = data.latency_by_name || {};
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
                    if (data.you.audio_last_seen) {
                        lastServerAudioSeenMs = data.you.audio_last_seen * 1000;
                        const nowMs = Date.now();
                        const serverStaleMs = Math.max(4000, getSilenceInterventionMs());
                        const localActiveWindow = Math.max(2000, getSilenceInterventionMs());
                        const localRecentlyActive = micHealth.lastLocalAudioAt && (performance.now() - micHealth.lastLocalAudioAt < localActiveWindow);
                        if (micHealth.stream && nowMs - lastServerAudioSeenMs > serverStaleMs) {
                            if (nowMs - serverAudioWarningAt > 15000) {
                                serverAudioWarningAt = nowMs;
                                if (localRecentlyActive) {
                                    setMicStatusMessage('Connection unstable — reconnecting…', {severity: 'warn'});
                                    try { window.triggerConnectionIntervention && window.triggerConnectionIntervention('server_audio'); } catch (e) {}
                                    sendConnectionNotification('Connection unstable — reconnecting to your room.');
                                } else {
                                    setMicStatusMessage('Microphone appears silent. If this is unexpected, check mic access.', {severity: 'warn'});
                                }
                            }
                        }
                    }
                }
                if (data.control) {
                    controlOwner = data.control.owner;
                    controlName = data.control.owner_name;
                    // update control UI if the control tab is present
                    try { updateControlUI(); } catch (e) {}
                    try { updateControlPasswordState(data.control); } catch (e) {}
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
    setInterval(sendClientMetrics, 1000);

    // Start a WebRTC session with the server: create RTCPeerConnection, capture local mic,
    // send SDP offer to server (/api?action=start_webrtc) and apply returned SDP answer.
    async function startWebRTCSession() {
        if (CONTROL_ONLY_MODE) {
            throw new Error('Control-only mode: WebRTC audio disabled');
        }
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
            attachMicStreamToMeter(localStream);
            handleFirstPermissionReloadHint();
        } catch (e) {
            printLog('getUserMedia failed: ' + e);
            // proceed without local audio if permission denied
            showMicReloadPrompt('Microphone permission failed. Reload and grant access.');
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
        setMicStatusMessage('Microphone connected to the server.', {severity: 'ok'});
        return pc;
    }

    // Reconnect logic with exponential backoff
    let _reconnectAttempts = 0;
    let _reconnectTimer = null;
    function tryScheduleReconnect() {
        if (CONTROL_ONLY_MODE) return;
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
                    if (payload && payload.capacity) {
                        setRoomCapacityState(payload.capacity);
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
    if (CONTROL_ONLY_MODE) {
        printLog('Control-only mode: blocking legacy startMicrophone request.');
        try { setStatus('Control-only mode: microphone disabled.'); } catch (e) {}
        applyControlOnlyModeUI();
        return;
    }
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
    if (CONTROL_ONLY_MODE) {
        return;
    }
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
    if (CONTROL_ONLY_MODE) {
        return;
    }
    printLog('Adding microphone to session...')

    let track = stream.getTracks()[0]
    pc.addTrack(track, stream)

    createOffer()
}

function skipMic(err) {
    if (CONTROL_ONLY_MODE) {
        return;
    }
    printLog('Skipping microphone configuration: '+err)
}

async function createOffer() {
    if (CONTROL_ONLY_MODE) {
        return;
    }
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
    if (CONTROL_ONLY_MODE) {
        return;
    }
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
    if (CONTROL_ONLY_MODE) {
        applyControlOnlyModeUI();
        return;
    }
    if (typeof pc === 'undefined') {
        return
    }

    printLog('Stopping session...')

    setStatus('Disconnecting...');
    stopMicMeter();
    if (!micHealth.reloadPromptShown) {
        setMicStatusMessage('Microphone idle.', {severity: 'info'});
    }

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
    const controlArea = document.getElementById('controlArea');
    const kickListEl = document.getElementById('kickList');
    const kickHelpEl = document.getElementById('kickHelp');

    let controlOwner = null;
    let controlName = null;
    let controlHasLocalLock = false;
    let controlShadowValue = controlTextInput ? (controlTextInput.value || '') : '';
    let controlSyncScheduled = false;
    let controlSyncChain = Promise.resolve();
    let controlCompositionActive = false;
    let playlistControlsLocked = false;

    function updateControlUI() {
        const localName = localStorage.getItem('userName') || '';
        const hasLock = !!controlOwner && !!controlName && !!localName && (controlName === localName);
        if (!controlOwner) {
            controlOwnerDiv.textContent = 'Control: free';
            controlAcquireBtn.style.display = 'inline-block';
            if (controlReleaseBtn) controlReleaseBtn.style.display = 'none';
        } else {
            controlOwnerDiv.textContent = `Control: ${controlName || controlOwner}`;
            controlAcquireBtn.style.display = 'none';
            if (controlReleaseBtn) controlReleaseBtn.style.display = 'inline-block';
        }
        const allowInput = hasLock && !playlistControlsLocked;
        if (controlTextInput) controlTextInput.disabled = !allowInput;
        const prevLockState = controlHasLocalLock;
        controlHasLocalLock = hasLock;
        if (!controlHasLocalLock) {
            controlShadowValue = controlTextInput ? (controlTextInput.value || '') : '';
        } else if (!prevLockState && controlHasLocalLock) {
            scheduleControlSync('lock-gained');
        }
        if (keyboardButtonsDiv) {
            keyboardButtonsDiv.classList.toggle('playlist-controls-disabled', playlistControlsLocked);
        }
        if (typeof window.setCapacityControlEditable === 'function') {
            try {
                window.setCapacityControlEditable(hasLock);
            } catch (e) {
                // ignore
            }
        }
        renderKickList();
    }

    window.__setPlaylistControlsLocked = function(flag) {
        playlistControlsLocked = !!flag;
        updateControlUI();
    };

    async function postControlKeystroke(key) {
        if (!key) return false;
        try {
            const res = await fetch('/control/keystroke', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                credentials: 'include',
                body: JSON.stringify({key})
            });
            if (!res.ok) {
                throw new Error('HTTP ' + res.status);
            }
            const data = await res.json();
            if (!data || !data.success) {
                throw new Error((data && data.error) || 'Keystroke failed');
            }
            return true;
        } catch (e) {
            printLog('Control keystroke error: ' + e.message);
            throw e;
        }
    }

    async function sendBackspaces(count) {
        let remaining = Number(count) || 0;
        if (remaining <= 0) return;
        while (remaining > 0) {
            await postControlKeystroke('Backspace');
            remaining -= 1;
        }
    }

    async function sendTextChunk(text) {
        if (!text) return;
        for (const ch of text) {
            await postControlKeystroke(ch);
        }
    }

    function classifyControlDiff(prev, next) {
        if (prev === next) return {type: 'none'};
        if (next.startsWith(prev)) {
            return {type: 'append', text: next.slice(prev.length)};
        }
        if (prev.startsWith(next)) {
            return {type: 'truncate', count: prev.length - next.length};
        }
        return {type: 'replace'};
    }

    async function performControlSync(targetValue, reason) {
        const prevValue = controlShadowValue || '';
        if (targetValue === prevValue) return;
        const diff = classifyControlDiff(prevValue, targetValue);
        if (diff.type === 'append' && diff.text) {
            await sendTextChunk(diff.text);
        } else if (diff.type === 'truncate' && diff.count > 0) {
            await sendBackspaces(diff.count);
        } else {
            if (prevValue.length > 0) {
                await sendBackspaces(prevValue.length);
            }
            if (targetValue.length > 0) {
                await sendTextChunk(targetValue);
            }
        }
        controlShadowValue = targetValue;
    }

    function scheduleControlSync(reason) {
        if (!controlTextInput) return;
        if (!controlHasLocalLock) return;
        if (controlCompositionActive) return;
        if (controlTextInput.value === controlShadowValue) return;
        if (controlSyncScheduled) return;
        controlSyncScheduled = true;
        controlSyncChain = controlSyncChain
            .then(async () => {
                try {
                    while (controlHasLocalLock && !controlCompositionActive && controlTextInput && controlTextInput.value !== controlShadowValue) {
                        const nextValue = controlTextInput.value || '';
                        await performControlSync(nextValue, reason);
                    }
                } finally {
                    controlSyncScheduled = false;
                }
            })
            .catch((err) => {
                controlSyncScheduled = false;
                printLog('Control sync failed: ' + err.message);
                setTimeout(() => {
                    if (controlHasLocalLock && !controlCompositionActive) {
                        scheduleControlSync('retry');
                    }
                }, 750);
            });
    }

    async function fetchControlStatus() {
        try {
            const res = await fetch('/control/status');
            const data = await res.json();
            if (data) {
                controlOwner = data.owner;
                controlName = data.owner_name;
                try { updateControlPasswordState(data); } catch (e) {}
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
                enforceControlCaretAtEnd({scrollIntoView: true});
            }
        } catch (e) {}
    }

    function enforceControlCaretAtEnd(options = {}) {
        if (!controlTextInput) return;
        try {
            if (options.forceFocus) {
                controlTextInput.focus();
            }
            const len = controlTextInput.value.length;
            if (controlTextInput.selectionStart !== len || controlTextInput.selectionEnd !== len) {
                controlTextInput.setSelectionRange(len, len);
            }
            if (options.scrollIntoView) {
                controlTextInput.scrollLeft = controlTextInput.scrollWidth;
            }
        } catch (e) {}
    }

    if (controlAcquireBtn) {
        controlAcquireBtn.addEventListener('click', async () => { await acquireControl(); focusControlInput(); });
    }
    if (controlReleaseBtn) {
        controlReleaseBtn.addEventListener('click', async () => { await releaseControl(); focusControlInput(); });
    }

    if (controlTextInput) {
        controlShadowValue = controlTextInput.value || '';
        controlTextInput.addEventListener('input', () => {
            if (!controlHasLocalLock) {
                controlShadowValue = controlTextInput.value || '';
                enforceControlCaretAtEnd();
                return;
            }
            scheduleControlSync('input');
            enforceControlCaretAtEnd();
        });
        controlTextInput.addEventListener('compositionstart', () => {
            controlCompositionActive = true;
        });
        controlTextInput.addEventListener('compositionend', () => {
            controlCompositionActive = false;
            if (controlHasLocalLock) {
                scheduleControlSync('composition');
            }
            enforceControlCaretAtEnd();
        });
        controlTextInput.addEventListener('paste', () => {
            if (!controlHasLocalLock) return;
            setTimeout(() => {
                scheduleControlSync('paste');
                enforceControlCaretAtEnd();
            }, 0);
        });
        controlTextInput.addEventListener('keydown', (ev) => {
            if (!controlHasLocalLock) return;
            if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
            const specialKeys = ['Enter', 'Escape', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'];
            if (specialKeys.includes(ev.key)) {
                ev.preventDefault();
                postControlKeystroke(ev.key).catch(() => {});
            }
            setTimeout(() => enforceControlCaretAtEnd(), 0);
        });
        controlTextInput.addEventListener('focus', () => enforceControlCaretAtEnd({scrollIntoView: true}));
        controlTextInput.addEventListener('click', () => enforceControlCaretAtEnd());
        controlTextInput.addEventListener('keyup', () => enforceControlCaretAtEnd());
        ['mousedown', 'touchstart'].forEach((evtName) => {
            controlTextInput.addEventListener(evtName, (ev) => {
                ev.preventDefault();
                enforceControlCaretAtEnd({forceFocus: true, scrollIntoView: true});
            }, {passive: false});
        });
        ['mouseup', 'touchend'].forEach((evtName) => {
            controlTextInput.addEventListener(evtName, () => {
                setTimeout(() => enforceControlCaretAtEnd({scrollIntoView: true}), 0);
            }, {passive: evtName === 'touchend' ? false : true});
        });
        document.addEventListener('selectionchange', () => {
            if (document.activeElement === controlTextInput) {
                enforceControlCaretAtEnd();
            }
        });
    }

    setInterval(() => {
        if (!controlTextInput) return;
        if (!controlHasLocalLock) return;
        if (controlCompositionActive) return;
        if (controlTextInput.value === controlShadowValue) return;
        scheduleControlSync('interval');
    }, 1200);

    const searchBtn = document.getElementById('SearchBtn');
    if (searchBtn) {
        searchBtn.addEventListener('click', () => {
            try {
                if (controlTextInput) {
                    const ev = new KeyboardEvent('keydown', { key: 'J', bubbles: true, cancelable: true });
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

    const playlistCard = document.getElementById('playlistModeCard');
    if (playlistCard) {
        const playlistStatusEl = document.getElementById('playlistModeStatus');
        const playlistCurrentEl = document.getElementById('playlistCurrentSong');
        const playlistNextEl = document.getElementById('playlistNextSong');
        const playlistCountdownInput = document.getElementById('playlistCountdownInput');
        const playlistToggleBtn = document.getElementById('playlistToggleBtn');
        const playlistAutoAddNote = document.getElementById('playlistAutoAddNote');
        const playlistCountdownConfiguredEl = document.getElementById('playlistCountdownConfigured');
        const playlistCountdownStateEl = document.getElementById('playlistCountdownState');
        let playlistStatusData = null;

        function renderPlaylistState(data) {
            if (!data) return;
            playlistStatusData = data;
            if (playlistStatusEl) {
                playlistStatusEl.textContent = data.status_text || data.status || 'Idle';
            }
            if (playlistCurrentEl) {
                playlistCurrentEl.textContent = data.current_song || '—';
            }
            if (playlistNextEl) {
                playlistNextEl.textContent = data.next_song || '—';
            }
            if (playlistCountdownInput && document.activeElement !== playlistCountdownInput) {
                const secondsVal = Number(data.countdown_seconds);
                if (Number.isFinite(secondsVal) && secondsVal > 0) {
                    playlistCountdownInput.value = secondsVal;
                }
            }
            if (playlistCountdownConfiguredEl) {
                const configured = Number(data.countdown_seconds);
                playlistCountdownConfiguredEl.textContent = Number.isFinite(configured) && configured > 0 ? `${configured} s` : '—';
            }
            if (playlistCountdownStateEl) {
                const remaining = Number(data.countdown_remaining);
                if (data.countdown_active && Number.isFinite(remaining)) {
                    playlistCountdownStateEl.textContent = `${Math.max(0, remaining)} s remaining`;
                } else {
                    playlistCountdownStateEl.textContent = data.status_text || 'Idle';
                }
            }
            if (playlistToggleBtn) {
                playlistToggleBtn.textContent = data.enabled ? 'Disable Playlist Mode' : 'Enable Playlist Mode';
            }
            if (playlistCountdownInput) {
                playlistCountdownInput.disabled = !data.enabled;
            }
            if (playlistAutoAddNote) {
                playlistAutoAddNote.textContent = data.auto_added ? `Auto-added ${data.auto_added} random song${data.auto_added === 1 ? '' : 's'}` : '';
            }
            if (window.__setPlaylistControlsLocked) {
                window.__setPlaylistControlsLocked(!!data.lock_controls);
            }
        }

        let playlistFetchInFlight = false;
        let lastPlaylistFetch = 0;
        async function fetchPlaylistStatus(force=false) {
            if (playlistFetchInFlight) return;
            const now = Date.now();
            if (!force && now - lastPlaylistFetch < 900) {
                return;
            }
            playlistFetchInFlight = true;
            try {
                const res = await fetch('/playlist/status', {credentials: 'include'});
                const body = await res.json();
                if (!body || !body.success) {
                    throw new Error((body && body.error) || 'status failed');
                }
                renderPlaylistState(body);
                lastPlaylistFetch = Date.now();
            } catch (err) {
                printLog('Playlist status error: ' + err.message);
            } finally {
                playlistFetchInFlight = false;
            }
        }

        async function togglePlaylistMode() {
            const targetEnabled = !(playlistStatusData && playlistStatusData.enabled);
            const payload = { enabled: targetEnabled };
            const secondsVal = Number(playlistCountdownInput ? playlistCountdownInput.value : NaN);
            if (Number.isFinite(secondsVal) && secondsVal > 0) {
                payload.countdown_seconds = secondsVal;
            }
            try {
                playlistToggleBtn && (playlistToggleBtn.disabled = true);
                const res = await fetch('/playlist/toggle', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    credentials: 'include',
                    body: JSON.stringify(payload)
                });
                const body = await res.json();
                if (!body || !body.success) {
                    throw new Error((body && body.error) || 'toggle failed');
                }
                renderPlaylistState(body.state || body);
            } catch (err) {
                printLog('Playlist toggle error: ' + err.message);
            } finally {
                if (playlistToggleBtn) playlistToggleBtn.disabled = false;
            }
        }

        if (playlistToggleBtn) {
            playlistToggleBtn.addEventListener('click', togglePlaylistMode);
        }
        if (playlistCountdownInput) {
            playlistCountdownInput.addEventListener('change', () => {
                const val = Number(playlistCountdownInput.value);
                if (!Number.isFinite(val) || val <= 0) {
                    const fallback = (playlistStatusData && playlistStatusData.countdown_seconds) || 15;
                    playlistCountdownInput.value = fallback;
                }
            });
        }

        fetchPlaylistStatus(true);
        setInterval(() => fetchPlaylistStatus(false), 1000);
    }

    // Poll control status every 2s
    fetchControlStatus();
    setInterval(fetchControlStatus, 2000);

    function renderKickList() {
        if (!kickListEl) return;
        const localName = localStorage.getItem('userName') || '';
        const hasLock = !!controlOwner && !!controlName && !!localName && (controlName === localName);
        if (kickHelpEl) {
            kickHelpEl.textContent = hasLock ? 'Tap a player to remove them.' : 'Acquire control to kick a player.';
        }
        const state = window.roomsState || {};
        const entries = [];
        Object.keys(state).forEach((room) => {
            const members = Array.isArray(state[room]) ? state[room] : [];
            members.forEach((name) => entries.push({room, name}));
        });
        kickListEl.innerHTML = '';
        if (!entries.length) {
            const empty = document.createElement('div');
            empty.textContent = 'No players connected.';
            empty.style.color = '#666';
            kickListEl.appendChild(empty);
            return;
        }
        entries.forEach((entry) => {
            const row = document.createElement('div');
            row.style.display = 'flex';
            row.style.justifyContent = 'space-between';
            row.style.alignItems = 'center';
            row.style.gap = '8px';

            const label = document.createElement('div');
            label.textContent = `${entry.name} · ${entry.room}`;
            label.style.flex = '1';

            const btn = document.createElement('button');
            btn.textContent = 'Kick';
            btn.style.flex = '0 0 auto';
            btn.style.background = '#ef4444';
            btn.style.color = '#fff';
            btn.style.border = 'none';
            btn.style.borderRadius = '8px';
            btn.style.padding = '6px 10px';
            btn.disabled = !hasLock;
            btn.addEventListener('click', async () => {
                if (!hasLock) return;
                const confirmed = window.confirm(`Kick ${entry.name} from ${entry.room}?`);
                if (!confirmed) return;
                btn.disabled = true;
                try {
                    const res = await fetch('/rooms/kick', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        credentials: 'include',
                        body: JSON.stringify({name: entry.name})
                    });
                    const data = await res.json();
                    if (!data || !data.success) {
                        throw new Error((data && data.error) || 'Kick failed');
                    }
                    if (data.rooms) {
                        window.roomsState = data.rooms;
                    }
                    printLog(`${entry.name} was kicked.`);
                } catch (e) {
                    printLog('Kick error: ' + e.message);
                } finally {
                    btn.disabled = false;
                    renderKickList();
                }
            });

            row.appendChild(label);
            row.appendChild(btn);
            kickListEl.appendChild(row);
        });
    }

    setInterval(renderKickList, 2000);

    // Release control on unload if we are the owner
    window.addEventListener('beforeunload', () => {
        try { window.releaseControl && window.releaseControl(); } catch(e){}
    });

});

// Bottom video behavior: clicking the banner requests fullscreen playback
document.addEventListener('DOMContentLoaded', function() {
    try {
        const video = document.getElementById('lockVideo');
        if (!video) return;

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

document.addEventListener('DOMContentLoaded', function() {
    try {
        updateLockScreenVideo(currentRoom);
    } catch (e) {
        console.warn('Failed to update lock screen video on load:', e);
    }
});