/**
 * Live Lean editor for the problem page.
 *
 * Connects to the WebSocket LSP proxy (JSON-RPC 2.0) for real-time goals/diagnostics, wires up
 * the CodeMirror editor(s), and handles the Run / Submit actions. Server-rendered values
 * (problem id + the run/submit URLs) are read from `#lean-form`'s data-* attributes. Loaded with
 * `defer` from problem_detail.html, so the DOM is parsed before this runs and the
 * DOMContentLoaded handler below still fires (defer scripts run before that event).
 */
(function () {
    'use strict';

    function setLeanStatus(text) {
        const el = document.getElementById('lean-status');
        if (el) el.textContent = text;
    }

    // Cross-window lock: when another window holds this user's single Lean instance, grey out
    // the editor block and show an overlay with a "Use Lean here" takeover button.
    function showLeanLock(message) {
        const block = document.getElementById('lean-editor-block');
        const overlay = document.getElementById('lean-lock-overlay');
        const text = document.getElementById('lean-lock-text');
        if (text && message) text.textContent = message;
        if (block) block.classList.add('locked');
        if (overlay) overlay.hidden = false;
    }

    function hideLeanLock() {
        const block = document.getElementById('lean-editor-block');
        const overlay = document.getElementById('lean-lock-overlay');
        if (block) block.classList.remove('locked');
        if (overlay) overlay.hidden = true;
    }

    function setTabText(id, text) {
        const el = document.getElementById(id);
        if (el) el.textContent = text;
    }

    // Shown when Lean reports no remaining goals; also the trigger for the green "complete" styling.
    const GOALS_COMPLETE_MESSAGE = 'No goals — proof complete! 🎉';

    // Write the Goals body and tint its section green only when the proof is complete.
    function setGoals(text) {
        setTabText('lean-goals', text);
        const section = document.getElementById('lean-goals')?.closest('.lean-panel');
        if (section) {
            section.classList.toggle('goals-complete', text === GOALS_COMPLETE_MESSAGE);
        }
    }

    /**
     * Shared renderer for the Goals / Messages / Errors panels, used by both the live-LSP
     * diagnostics and the run/submit HTTP response so the two paths (and their empty-state
     * wording) can't drift apart. `sep` joins entries ('\n\n' between LSP diagnostics, '\n'
     * between parsed runner-output lines). Pass `goals: undefined` to leave the goals panel
     * untouched — the LSP path fills it asynchronously via $/lean/plainGoal.
     */
    function renderPanels({ goals, messages, errors, errored = false, sep = '\n' }) {
        if (goals !== undefined) {
            if (goals.length) {
                setGoals(goals.join(sep));
            } else if (errors.length || errored) {
                // No goals, but the run errored — don't claim the proof is complete.
                setGoals('No goals produced.');
            } else {
                setGoals(GOALS_COMPLETE_MESSAGE);
            }
        }
        setTabText('lean-messages', messages.length ? messages.join(sep) : 'No messages.');
        setTabText('lean-errors', errors.length ? errors.join(sep) : 'No errors.');
        setErrorCount(errors.length);
    }

    class LeanLSPClient {
        constructor(problemPk) {
            this.ws = null;
            this.messageId = 1;
            this.version = 1;
            this.initialized = false;
            this.problemPk = problemPk;
            // Canonical client-side document URI; the server maps this onto the assembled file.
            this.docUri = 'file:///pisa/problem.lean';
            this.pendingGoals = new Set(); // ids of in-flight $/lean/plainGoal requests
            this.locked = false; // another window holds this user's Lean instance
            this.retriedQuick = false; // used the one-shot reload-race retry?
            this.retryTimer = null;
            this.pollTimer = null;
            this.visHandler = null;
        }

        connect(takeover = false) {
            const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
            const query = takeover ? '?takeover=1' : '';
            const url = `${protocol}://${window.location.host}/ws/lean-lsp/${this.problemPk}/${query}`;

            this.ws = new WebSocket(url);
            this.ws.onopen = () => this.onOpen();
            this.ws.onmessage = (event) => this.onMessage(event);
            this.ws.onerror = (error) => {
                console.warn('LSP WebSocket error:', error);
                this.onError();
            };
            this.ws.onclose = () => {
                console.info('LSP WebSocket closed');
                this.initialized = false;
                // Stay quiet while a reconnect is pending (reload race) or the overlay is up.
                if (!this.locked && !this.retryTimer) setLeanStatus('Live feedback off');
            };
        }

        // "Use Lean here": drop our socket and reconnect with takeover, evicting the holder.
        takeOver() {
            hideLeanLock();
            this.locked = false;
            this.clearRetry();
            this.stopPolling();
            setLeanStatus('Connecting…');
            if (this.ws) {
                try {
                    this.ws.onclose = null;
                    this.ws.close();
                } catch (e) { /* already closed */ }
                this.ws = null;
            }
            this.connect(true);
        }

        scheduleRetry(delay) {
            this.clearRetry();
            this.retryTimer = setTimeout(() => {
                this.retryTimer = null;
                const ws = this.ws;
                // Don't stack a second socket on a still-connecting/open one.
                if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
                    return;
                }
                this.connect(false);
            }, delay);
        }

        clearRetry() {
            if (this.retryTimer) {
                clearTimeout(this.retryTimer);
                this.retryTimer = null;
            }
        }

        // While the overlay is up, periodically attempt a passive reconnect so this tab takes
        // over automatically once the holding window releases the slot (a passive connect claims
        // a free slot server-side). Polls only while visible, and retries the moment the user
        // switches back to this tab.
        startPolling() {
            if (this.pollTimer) return;
            this.pollTimer = setInterval(() => {
                if (this.locked && !document.hidden) this.scheduleRetry(0);
            }, 5000);
            this.visHandler = () => {
                if (this.locked && !document.hidden) this.scheduleRetry(0);
            };
            document.addEventListener('visibilitychange', this.visHandler);
        }

        stopPolling() {
            if (this.pollTimer) {
                clearInterval(this.pollTimer);
                this.pollTimer = null;
            }
            if (this.visHandler) {
                document.removeEventListener('visibilitychange', this.visHandler);
                this.visHandler = null;
            }
        }

        onOpen() {
            setLeanStatus('Connecting…');
            this.send({
                jsonrpc: '2.0',
                id: this.messageId++,
                method: 'initialize',
                params: {
                    processId: null,
                    rootUri: null,
                    capabilities: {
                        textDocument: {
                            synchronization: { didChange: { incrementalSync: false } },
                        },
                    },
                },
            });
        }

        onMessage(event) {
            let message;
            try {
                message = JSON.parse(event.data);
            } catch (error) {
                console.warn('LSP message parse error:', error, event.data);
                return;
            }

            // Server-side status: live feedback unavailable for this problem, or the user's
            // single Lean instance is already open in another window.
            if (message.pisa) {
                const status = message.pisa.status;
                if (status === 'busy' || status === 'taken_over') {
                    this.initialized = false;
                    // A reload can momentarily collide with the previous connection's teardown.
                    // On the first "busy", retry once quickly before greying the editor, so a
                    // reload recovers without flashing the overlay. An explicit "taken_over"
                    // (another window stole the slot) goes straight to the overlay.
                    if (status === 'busy' && !this.retriedQuick && !this.locked) {
                        this.retriedQuick = true;
                        setLeanStatus('Connecting…');
                        this.scheduleRetry(800);
                        return;
                    }
                    this.locked = true;
                    showLeanLock(message.pisa.reason);
                    setLeanStatus('Paused — open in another window');
                    // Poll so this tab auto-promotes when the holding window releases the slot.
                    this.startPolling();
                    return;
                }
                console.info('Live feedback unavailable:', message.pisa.reason);
                this.initialized = false;
                setLeanStatus('Live feedback off');
                return;
            }
            // Response to one of our $/lean/plainGoal requests.
            if (message.id !== undefined && this.pendingGoals.has(message.id)) {
                this.pendingGoals.delete(message.id);
                this.onGoalResult(message.result);
                return;
            }
            // initialize response -> handshake complete; open the document.
            if (!message.method && message.result && message.result.capabilities) {
                this.initialized = true;
                this.locked = false;
                this.retriedQuick = false;
                this.clearRetry();
                this.stopPolling();
                hideLeanLock();
                this.send({ jsonrpc: '2.0', method: 'initialized', params: {} });
                this.didOpen(leanEditor ? leanEditor.getValue() : '');
                setLeanStatus('Live feedback on');
                return;
            }
            if (message.method === 'textDocument/publishDiagnostics') {
                this.onPublishDiagnostics(message.params);
            }
        }

        onPublishDiagnostics(params) {
            const diagnostics = params.diagnostics || [];
            const format = (d) => {
                const line = d.range?.start?.line ?? 0;
                const col = d.range?.start?.character ?? 0;
                return `L${line + 1}:${col + 1}  ${d.message}`;
            };
            const errors = diagnostics.filter((d) => d.severity === 1);
            const others = diagnostics.filter((d) => d.severity !== 1);
            renderPanels({ messages: others.map(format), errors: errors.map(format), sep: '\n\n' });
            setLeanStatus(errors.length ? `${errors.length} error${errors.length > 1 ? 's' : ''}` : 'No errors');
            // Diagnostics settled — refresh the goal at the cursor.
            this.requestGoals();
        }

        onGoalResult(result) {
            const goals = (result && result.goals) || [];
            setGoals(goals.length ? goals.join('\n\n') : GOALS_COMPLETE_MESSAGE);
        }

        onError() {
            // Don't clobber the status while a reconnect is pending or the overlay is up.
            if (!this.locked && !this.retryTimer) setLeanStatus('Live feedback off');
        }

        send(message) {
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify(message));
            }
        }

        didOpen(content) {
            this.version = 1;
            this.send({
                jsonrpc: '2.0',
                method: 'textDocument/didOpen',
                params: {
                    textDocument: { uri: this.docUri, languageId: 'lean', version: this.version, text: content },
                },
            });
            this.requestGoals();
        }

        didChange(content) {
            if (!this.initialized) return;
            this.version += 1;
            this.send({
                jsonrpc: '2.0',
                method: 'textDocument/didChange',
                params: {
                    textDocument: { uri: this.docUri, version: this.version },
                    contentChanges: [{ text: content }],
                },
            });
        }

        requestGoals() {
            if (!this.initialized || !leanEditor) return;
            const cursor = leanEditor.getCursor();
            const id = this.messageId++;
            this.pendingGoals.add(id);
            this.send({
                jsonrpc: '2.0',
                id,
                method: '$/lean/plainGoal',
                params: {
                    textDocument: { uri: this.docUri },
                    position: { line: cursor.line, character: cursor.ch },
                },
            });
        }
    }

    let lspClient;

    function debounce(func, delay) {
        let timeoutId;
        return function (...args) {
            clearTimeout(timeoutId);
            timeoutId = setTimeout(() => func.apply(this, args), delay);
        };
    }

    let leanEditor;
    const editableEditors = new Map();

    function saveEditorValues() {
        if (leanEditor) {
            leanEditor.save();
        }
        editableEditors.forEach((editor) => editor.save());
    }

    function showEditorFallback() {
        const fallback = document.getElementById('lean-editor-fallback');
        if (fallback) {
            fallback.hidden = false;
        }
    }

    function showDiagnostics(message, severity = 'error') {
        const diagnostics = document.getElementById('lean-diagnostics');
        diagnostics.textContent = message;
        diagnostics.classList.remove('hidden', 'error', 'success', 'warning');
        diagnostics.classList.add('fallback-message', severity);
    }

    document.addEventListener('DOMContentLoaded', function () {
        const leanForm = document.getElementById('lean-form');

        try {
            if (typeof CodeMirror === 'undefined') {
                throw new Error('CodeMirror is not available');
            }

            document.querySelectorAll('#lean-code, .editable-code').forEach((textarea) => {
                const editor = CodeMirror.fromTextArea(textarea, {
                    mode: 'lean',
                    theme: 'default',
                    lineNumbers: true,
                    matchBrackets: true,
                    indentUnit: 2,
                    indentWithTabs: false,
                    autofocus: textarea.id === 'lean-code',
                    tabSize: 2,
                    viewportMargin: Infinity,
                });

                if (textarea.id === 'lean-code') {
                    leanEditor = editor;

                    // Live feedback is scoped to the single main editor: push edits to the LSP
                    // (debounced) and refresh the goal at the cursor as it moves.
                    editor.on('change', debounce(() => {
                        if (lspClient && lspClient.initialized) {
                            lspClient.didChange(editor.getValue());
                        }
                    }, 500));
                    editor.on('cursorActivity', debounce(() => {
                        if (lspClient && lspClient.initialized) {
                            lspClient.requestGoals();
                        }
                    }, 250));
                } else {
                    editableEditors.set(textarea, editor);
                }
            });

            // Initialize LSP client and connect (scoped to this problem).
            lspClient = new LeanLSPClient(Number(leanForm.dataset.problemPk));
            lspClient.connect();

            const useHereButton = document.getElementById('lean-use-here');
            if (useHereButton) {
                useHereButton.addEventListener('click', () => lspClient.takeOver());
            }
        } catch (error) {
            console.warn('CodeMirror initialization failed:', error);
            showEditorFallback();
        }

        // Run / Submit work whether or not CodeMirror loaded (plain-textarea fallback), so wire
        // these up outside the try above.
        if (leanForm) {
            leanForm.addEventListener('submit', (event) => {
                event.preventDefault();
                runLean();
            });
        }
        const submitButton = document.getElementById('lean-submit');
        if (submitButton) {
            submitButton.addEventListener('click', submitSolution);
        }

        initLeanPanels();
    });


    // Only the Errors section is collapsible (and ships collapsed). Its heading button
    // toggles the panel's `collapsed` class; Goals and Messages are always-visible sections.
    function initLeanPanels() {
        document.querySelectorAll('.lean-panel-header').forEach((header) => {
            header.addEventListener('click', () => {
                const panel = header.closest('.lean-panel');
                if (!panel) {
                    return;
                }
                const collapsed = panel.classList.toggle('collapsed');
                header.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
            });
        });
    }

    // The Errors panel is collapsed by default, so surface its count on the header
    // (e.g. "Errors (3)") to flag problems without forcing the panel open.
    function setErrorCount(count) {
        const header = document.getElementById('lean-errors-header');
        if (header) {
            header.textContent = count ? `Errors (${count})` : 'Errors';
        }
    }

    function renderLeanFeedback(data) {
        renderPanels({
            goals: data.goals || [],
            messages: data.messages || [],
            errors: data.errors || [],
            // A failed run with no parsed errors (e.g. the sandbox itself broke) must not
            // render the celebratory empty-goals state.
            errored: Boolean(data.error) || (data.returncode !== undefined && data.returncode !== 0),
        });

        const diagnostics = document.getElementById('lean-diagnostics');
        if (
            (!data.goals || data.goals.length === 0) &&
            (!data.messages || data.messages.length === 0) &&
            (!data.errors || data.errors.length === 0)
        ) {
            const message = data.error || data.stderr || data.stdout || 'Lean ran with no errors :)';
            const severity = data.error || data.stderr || data.returncode !== 0 ? 'error' : 'success';
            showDiagnostics(message, severity);
        } else {
            diagnostics.classList.add('hidden');
        }
    }

    async function runLean() {
        const status = document.getElementById('lean-status');
        const diagnostics = document.getElementById('lean-diagnostics');
        const form = document.getElementById('lean-form');

        status.textContent = 'Running';
        diagnostics.textContent = '';
        diagnostics.classList.add('hidden');

        saveEditorValues();
        const formData = new FormData(form);

        const response = await fetch(form.dataset.runUrl, {
            method: 'POST',
            headers: {
                'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value,
            },
            body: formData,
        });

        if (!response.ok) {
            const msg = 'Unable to run Lean. Server returned ' + response.status;
            status.textContent = 'Error';
            showDiagnostics(msg, 'error');
            return;
        }

        const data = await response.json();
        if (data.error) {
            status.textContent = 'Error';
            showDiagnostics(data.error, 'error');
            return;
        }

        status.textContent = data.returncode === 0 ? 'OK' : 'Failed (exit ' + data.returncode + ')';
        renderLeanFeedback(data);
    }

    async function submitSolution() {
        const status = document.getElementById('lean-status');
        const diagnostics = document.getElementById('lean-diagnostics');
        const form = document.getElementById('lean-form');

        status.textContent = 'Submitting';
        diagnostics.classList.add('hidden');

        saveEditorValues();
        const formData = new FormData(form);

        const response = await fetch(form.dataset.submitUrl, {
            method: 'POST',
            headers: {
                'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value,
            },
            body: formData,
        });

        if (!response.ok) {
            const msg = 'Unable to submit. Server returned ' + response.status;
            status.textContent = 'Error';
            showDiagnostics(msg, 'error');
            return;
        }

        const data = await response.json();
        if (data.error) {
            status.textContent = 'Error';
            showDiagnostics(data.error, 'error');
            return;
        }

        if (data.status === 'passed') {
            status.textContent = 'Submission received';
            const score = data.score ?? 0;
            const possiblePoints = data.possible_points ?? score;
            showDiagnostics(
                `Submission received (${score} / ${possiblePoints})`,
                'success'
            );
        } else if (data.status === 'failed') {
            status.textContent = 'Submission received';
            const score = data.score ?? 0;
            const possiblePoints = data.possible_points ?? score;
            const severity = score > 0 ? 'warning' : 'error';
            showDiagnostics(
                `Submission received (${score} / ${possiblePoints})`,
                severity
            );
        } else if (data.status === 'error') {
            status.textContent = 'Error';
            showDiagnostics(data.result || 'Submission error.', 'error');
        } else if (data.goals || data.messages || data.errors) {
            status.textContent = data.status || 'Submitted';
            renderLeanFeedback(data);
        } else {
            status.textContent = data.status || 'Submitted';
            showDiagnostics(data.result || 'Submission completed.', 'success');
        }
    }
})();
