// Control Center Client Application
(() => {
    const urlParams = new URLSearchParams(window.location.search);
    const token = urlParams.get('token') || '';

    // DOM Elements
    const statusBadge = document.getElementById('status-badge');
    const activeRunIdEl = document.getElementById('active-run-id');
    const btnStop = document.getElementById('btn-stop');
    const goalInput = document.getElementById('goal-input');
    const modeSelect = document.getElementById('mode-select');
    const btnStart = document.getElementById('btn-start');

    const cardApproval = document.getElementById('card-approval');
    const approvalCategory = document.getElementById('approval-category');
    const approvalSummary = document.getElementById('approval-summary');
    const approvalActionType = document.getElementById('approval-action-type');
    const approvalReasons = document.getElementById('approval-reasons');
    const btnApprove = document.getElementById('btn-approve');
    const btnApproveRun = document.getElementById('btn-approve-run');
    const btnDeny = document.getElementById('btn-deny');

    const cardQuestion = document.getElementById('card-question');
    const questionText = document.getElementById('question-text');
    const answerInput = document.getElementById('answer-input');
    const btnSubmitAnswer = document.getElementById('btn-submit-answer');

    const planRevision = document.getElementById('plan-revision');
    const planStepsList = document.getElementById('plan-steps-list');
    const screenPreviewImg = document.getElementById('screen-preview-img');
    const eventsLog = document.getElementById('events-log');
    const btnClearLog = document.getElementById('btn-clear-log');
    const runsHistoryList = document.getElementById('runs-history-list');

    let currentRunId = null;
    let activeApprovalReqId = null;
    let activeAnswerReqId = null;

    // Helper: Authenticated fetch
    async function apiFetch(path, options = {}) {
        const url = new URL(path, window.location.origin);
        if (token) {
            url.searchParams.set('token', token);
        }
        const headers = options.headers || {};
        if (token) {
            headers['X-LC-Token'] = token;
        }
        return fetch(url.toString(), { ...options, headers });
    }

    // Logger
    function logMessage(text, type = 'info') {
        const entry = document.createElement('div');
        entry.className = `log-entry log-${type}`;
        const time = new Date().toLocaleTimeString();
        entry.innerText = `[${time}] ${text}`;
        eventsLog.appendChild(entry);
        eventsLog.scrollTop = eventsLog.scrollHeight;
    }

    // Status updater
    function updateStatus(status) {
        statusBadge.className = 'badge';
        if (status === 'RUNNING') {
            statusBadge.classList.add('badge-running');
            statusBadge.innerText = 'RUNNING';
            btnStop.disabled = false;
        } else if (status === 'WAITING_APPROVAL' || status === 'WAITING_USER') {
            statusBadge.classList.add('badge-waiting');
            statusBadge.innerText = status;
            btnStop.disabled = false;
        } else if (status === 'COMPLETED') {
            statusBadge.classList.add('badge-completed');
            statusBadge.innerText = 'COMPLETED';
            btnStop.disabled = true;
        } else if (status && status.startsWith('FAILED')) {
            statusBadge.classList.add('badge-failed');
            statusBadge.innerText = status;
            btnStop.disabled = true;
        } else {
            statusBadge.classList.add('badge-idle');
            statusBadge.innerText = status || 'IDLE';
            btnStop.disabled = true;
        }
    }

    // Update Plan List
    function updatePlan(plan) {
        if (!plan || !plan.steps || plan.steps.length === 0) {
            planStepsList.innerHTML = '<li class="empty-state">No active plan</li>';
            planRevision.innerText = 'Rev 0';
            return;
        }
        planRevision.innerText = `Rev ${plan.revision || 0}`;
        planStepsList.innerHTML = '';
        plan.steps.forEach(step => {
            const li = document.createElement('li');
            li.className = 'step-item';
            if (step.status === 'active') li.classList.add('active');
            if (step.status === 'done') li.classList.add('done');

            const icon = step.status === 'done' ? '✓' : (step.status === 'active' ? '▶' : '○');
            li.innerHTML = `<span>${icon}</span> <span>[${step.index}] ${step.description}</span>`;
            planStepsList.appendChild(li);
        });
    }

    function displayApproval(payload) {
        if (!payload) return;
        activeApprovalReqId = payload.request_id;
        approvalCategory.innerText = payload.category || 'CONFIRM';
        approvalSummary.innerText = payload.human_summary || 'Confirmation required';
        approvalActionType.innerText = payload.action?.type || '';
        approvalReasons.innerText = (payload.verdict?.reasons || []).join(', ');
        cardApproval.classList.remove('hidden');
        updateStatus('WAITING_APPROVAL');
    }

    function displayQuestion(payload) {
        if (!payload) return;
        activeAnswerReqId = payload.request_id;
        questionText.innerText = payload.question || 'Input requested';
        cardQuestion.classList.remove('hidden');
        updateStatus('WAITING_USER');
    }

    // WebSocket Connection
    function connectWebSocket() {
        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${wsProtocol}//${window.location.host}/ws?token=${encodeURIComponent(token)}`;
        const socket = new WebSocket(wsUrl);

        socket.onopen = () => {
            logMessage('WebSocket connected to runner feed.', 'success');
            syncServerStatus();
        };

        socket.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                handleServerEvent(data);
            } catch (err) {
                console.error('Failed to parse WS message', err);
            }
        };

        socket.onclose = () => {
            logMessage('WebSocket disconnected. Reconnecting in 2s...', 'error');
            setTimeout(connectWebSocket, 2000);
        };
    }

    // Event Handler
    function handleServerEvent(event) {
        const type = event.type;
        const payload = event.payload || {};

        if (type === 'preview_frame') {
            if (payload.image_base64) {
                screenPreviewImg.src = 'data:image/jpeg;base64,' + payload.image_base64;
            }
            return;
        }

        if (type === 'run_started') {
            currentRunId = event.run_id;
            activeRunIdEl.innerText = currentRunId;
            updateStatus('RUNNING');
            logMessage(`Run started: ${payload.goal || ''}`, 'action');
        } else if (type === 'step_started') {
            logMessage(`Step ${event.step_index || 0} started`, 'info');
        } else if (type === 'planner_proposal') {
            if (payload.plan) {
                updatePlan(payload.plan);
            }
            if (payload.action) {
                logMessage(`Planner proposed: ${payload.action.type} — ${payload.action.target_description || ''}`, 'action');
            }
        } else if (type === 'verdict') {
            logMessage(`Verdict: ${payload.tier} [${payload.category}] -> ${payload.decision}`, 'verdict');
        } else if (type === 'approval_requested') {
            displayApproval(payload);
            logMessage(`APPROVAL REQUIRED: ${payload.human_summary}`, 'verdict');
        } else if (type === 'user_input_requested') {
            displayQuestion(payload);
            logMessage(`USER INPUT REQUIRED: ${payload.question}`, 'verdict');
        } else if (type === 'action_started') {
            logMessage(`Executing: ${payload.action_type || ''}...`, 'action');
        } else if (type === 'action_finished') {
            cardApproval.classList.add('hidden');
            const res = payload.result || {};
            const status = res.success ? 'Success' : `Failed (${res.error?.code || 'error'})`;
            logMessage(`Action result: ${res.action_type} -> ${status} (${res.duration_ms || 0}ms)`, res.success ? 'success' : 'error');
        } else if (type === 'run_finished') {
            updateStatus(payload.status || 'FINISHED');
            cardApproval.classList.add('hidden');
            cardQuestion.classList.add('hidden');
            logMessage(`Run finished with status: ${payload.status}`, payload.status === 'COMPLETED' ? 'success' : 'error');
            loadRunsHistory();
        }
    }

    // Actions
    btnStart.addEventListener('click', async () => {
        const goal = goalInput.value.trim();
        if (!goal) {
            alert('Please enter a goal.');
            return;
        }
        const mode = modeSelect.value;
        btnStart.disabled = true;
        try {
            const resp = await apiFetch('/api/runs', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ goal, autonomy_mode: mode })
            });
            const data = await resp.json();
            if (resp.ok) {
                currentRunId = data.run_id;
                activeRunIdEl.innerText = currentRunId;
                updateStatus('RUNNING');
                logMessage(`Run created: ${currentRunId}`, 'info');
            } else if (data.detail && data.detail.includes('already currently active')) {
                const force = confirm('A run is currently active on the server. Do you want to stop it and start this new run?');
                if (force) {
                    const forceResp = await apiFetch('/api/runs', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ goal, autonomy_mode: mode, force: true })
                    });
                    const forceData = await forceResp.json();
                    if (forceResp.ok) {
                        currentRunId = forceData.run_id;
                        activeRunIdEl.innerText = currentRunId;
                        updateStatus('RUNNING');
                        logMessage(`Run created: ${currentRunId}`, 'info');
                    } else {
                        alert(`Failed to start run: ${forceData.detail || forceData.error}`);
                    }
                }
            } else {
                alert(`Failed to start run: ${data.detail || data.error}`);
            }
        } catch (err) {
            alert(`Error starting run: ${err.message}`);
        } finally {
            btnStart.disabled = false;
        }
    });

    btnStop.addEventListener('click', async () => {
        try {
            await apiFetch('/api/stop', { method: 'POST' });
            cardApproval.classList.add('hidden');
            cardQuestion.classList.add('hidden');
            updateStatus('STOPPED');
            activeRunIdEl.innerText = 'Stopped';
            btnStart.disabled = false;
            logMessage('Stop signal sent to runner.', 'error');
        } catch (err) {
            console.error('Stop request failed', err);
        }
    });

    async function sendApproval(decision) {
        cardApproval.classList.add('hidden');
        try {
            await apiFetch(`/api/runs/${currentRunId || 'current'}/approve`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ decision, request_id: activeApprovalReqId })
            });
            logMessage(`Approval decision sent: ${decision}`, 'info');
        } catch (err) {
            console.error('Failed to submit approval', err);
        }
    }

    btnApprove.addEventListener('click', () => sendApproval('approved'));
    btnApproveRun.addEventListener('click', () => sendApproval('approved_for_run'));
    btnDeny.addEventListener('click', () => sendApproval('denied'));

    btnSubmitAnswer.addEventListener('click', async () => {
        const answer = answerInput.value.trim();
        cardQuestion.classList.add('hidden');
        try {
            await apiFetch(`/api/runs/${currentRunId || 'current'}/answer`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ answer, request_id: activeAnswerReqId })
            });
            answerInput.value = '';
            logMessage(`Answer submitted: "${answer}"`, 'info');
        } catch (err) {
            console.error('Failed to submit answer', err);
        }
    });

    btnClearLog.addEventListener('click', () => {
        eventsLog.innerHTML = '';
    });

    // Load Runs History & Replay
    async function loadRunsHistory() {
        try {
            const resp = await apiFetch('/api/runs');
            if (!resp.ok) return;
            const runs = await resp.json();
            if (!runs || runs.length === 0) {
                runsHistoryList.innerHTML = '<p class="empty-state">No previous runs found.</p>';
                return;
            }
            runsHistoryList.innerHTML = '';
            runs.slice(0, 10).forEach(run => {
                const item = document.createElement('div');
                item.className = 'run-item';
                item.innerHTML = `
                    <div>
                        <strong>${run.run_id}</strong>
                        <div style="color: var(--text-secondary); font-size: 0.8rem;">${run.goal || 'No goal'}</div>
                    </div>
                    <button class="btn btn-secondary btn-sm" onclick="window.replayRun('${run.run_id}')">Replay</button>
                `;
                runsHistoryList.appendChild(item);
            });
        } catch (err) {
            console.error('Failed to load runs history', err);
        }
    }

    window.replayRun = async (runId) => {
        try {
            const resp = await apiFetch(`/api/runs/${runId}/replay`);
            if (!resp.ok) return;
            const replayData = await resp.json();
            eventsLog.innerHTML = `<div class="log-entry log-info">=== REPLAY OF ${runId} ===</div>`;
            (replayData.steps || []).forEach(step => {
                const act = step.action || {};
                const res = step.result || {};
                logMessage(`Step ${step.step_index}: ${act.type} (${res.success ? 'OK' : 'FAIL'})`, 'action');
            });
            if (replayData.summary) {
                logMessage(`Summary: ${replayData.summary}`, 'success');
            }
        } catch (err) {
            console.error('Replay error', err);
        }
    };

    async function syncServerStatus() {
        try {
            const resp = await apiFetch('/api/status');
            if (!resp.ok) return;
            const data = await resp.json();
            if (data.active_run) {
                currentRunId = data.active_run;
                activeRunIdEl.innerText = currentRunId;
                updateStatus(data.run_status || 'RUNNING');
                if (data.pending_approval) {
                    displayApproval(data.pending_approval);
                } else if (data.pending_question) {
                    displayQuestion(data.pending_question);
                }
            } else {
                updateStatus('IDLE');
                activeRunIdEl.innerText = 'No active run';
            }
        } catch (err) {
            console.error('Status sync failed', err);
        }
    }

    // Init
    connectWebSocket();
    loadRunsHistory();
    syncServerStatus();
})();
