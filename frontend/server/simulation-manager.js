const pm2 = require('pm2');
const fs = require('fs');
const path = require('path');
const yaml = require('js-yaml');
const { exec } = require('child_process');
const util = require('util');
const EventEmitter = require('events');
const WebSocket = require('ws');

const execPromise = util.promisify(exec);

// Use native fetch if available (Node.js 18+), otherwise require node-fetch
let fetch;
if (typeof globalThis.fetch === 'function') {
    fetch = globalThis.fetch;
} else {
    try {
        fetch = require('node-fetch');
    } catch (e) {
        console.warn('Warning: fetch not available. Install node-fetch or use Node.js 18+');
        fetch = null;
    }
}

/**
 * Simulation State Machine
 * Manages the lifecycle of simulation services with proper state transitions
 */
class SimulationManager extends EventEmitter {
    constructor(options = {}) {
        super();

        this.statusFile = options.statusFile || path.join(__dirname, '../../.scenario_status.json');
        this.configLauncherUrl = options.configLauncherUrl || 'http://127.0.0.1:5001';
        this.maxWaitTime = options.maxWaitTime || 300; // 5 minutes

        // State machine definition
        this.states = {
            IDLE: 'idle',        // Initial state, everything stopped
            STOPPED: 'stopped',  // Stopped after running
            LOADING: 'loading',
            LOADED: 'loaded',
            STARTING: 'starting',
            RUNNING: 'running',
            STOPPING: 'stopping',
            ERROR: 'error',
            PAUSED: 'paused' // Future: pause/resume capability
        };

        // Valid state transitions
        this.transitions = {
            [this.states.IDLE]: [this.states.LOADING, this.states.ERROR],
            [this.states.STOPPED]: [this.states.IDLE, this.states.LOADING, this.states.ERROR],
            [this.states.LOADING]: [this.states.LOADED, this.states.ERROR, this.states.STOPPED, this.states.IDLE],
            [this.states.LOADED]: [this.states.STARTING, this.states.STOPPED, this.states.IDLE, this.states.ERROR],
            [this.states.STARTING]: [this.states.RUNNING, this.states.ERROR, this.states.STOPPED, this.states.IDLE, this.states.LOADED],
            [this.states.RUNNING]: [this.states.STOPPING, this.states.LOADED, this.states.PAUSED, this.states.ERROR],
            [this.states.STOPPING]: [this.states.STOPPED, this.states.IDLE, this.states.ERROR],
            [this.states.ERROR]: [this.states.STOPPED, this.states.IDLE, this.states.LOADING],
            [this.states.PAUSED]: [this.states.RUNNING, this.states.STOPPING]
        };

        // Current state - always start as IDLE
        this.currentState = this.states.IDLE;
        this.currentScenario = null;
        this.stateHistory = [];
        this.pm2Connected = false;
        this.activeStateOperations = 0;
        this.recordingEnabled = false;

        // Ensure initial state is IDLE and all processes are stopped
        this.startupPromise = this.initializeState();
    }

    /**
     * Ensure initial state is IDLE and all processes are stopped
     */
    async initializeState() {
        try {
            const processes = await this.getProcessList();
            const relevantNames = new Set([
                'carla-headless',
                'sumo-server',
                'artery',
                'orchestrator'
            ]);

            const relevantProcesses = processes.filter(proc => relevantNames.has(proc.name));

            // If relevant processes exist, adopt them instead of killing them
            if (relevantProcesses.length > 0) {
                console.log('[SimulationManager] Adopting existing processes on initialization...');
                this.currentState = this.states.RUNNING;
                this.loadStateFromFile();
                if (!this.currentScenario) {
                    this.currentScenario = { scenario: 'scenarios/default.yaml', mode: 'sumo_cosim', town: 'Unknown' };
                }
                return;
            }

            // Always set state to IDLE on initialization if no processes exist
            this.currentState = this.states.IDLE;
            this.currentScenario = null;
            if (fs.existsSync(this.statusFile)) {
                try {
                    fs.unlinkSync(this.statusFile);
                } catch (err) {
                    console.warn('[SimulationManager] Unable to remove status file:', err.message);
                }
            }

            console.log('[SimulationManager] Initialized with state: IDLE');
        } catch (error) {
            console.error('[SimulationManager] Error initializing state:', error.message);
            // Ensure state is IDLE even on error
            this.currentState = this.states.IDLE;
            this.currentScenario = null;
            this.recordingEnabled = false;
        } finally {
            this.disconnectPM2();
        }
    }

    /**
     * Load state from status file on startup
     */
    loadStateFromFile() {
        try {
            if (fs.existsSync(this.statusFile)) {
                const status = JSON.parse(fs.readFileSync(this.statusFile, 'utf8'));
                this.currentState = status.state || this.states.IDLE;
                this.recordingEnabled = status.recording || false;
                if (status.scenario) {
                    this.currentScenario = {
                        scenario: status.scenario,
                        mode: status.mode,
                        town: status.town,
                        loadedAt: status.loadedAt,
                        startedAt: status.startedAt
                    };
                }
            }
        } catch (error) {
            console.error('[SimulationManager] Error loading state from file:', error);
        }
    }

    /**
     * Get current state
     */
    getState() {
        return {
            state: this.currentState,
            scenario: this.currentScenario,
            history: this.stateHistory.slice(-10) // Last 10 transitions
        };
    }

    /**
     * Check if transition is valid
     */
    canTransition(newState) {
        const validTransitions = this.transitions[this.currentState] || [];
        return validTransitions.includes(newState);
    }

    /**
     * Transition to new state (with validation)
     */
    async transition(newState, context = {}) {
        if (!this.canTransition(newState)) {
            const error = new Error(
                `Invalid state transition: ${this.currentState} -> ${newState}. ` +
                `Valid transitions: ${this.transitions[this.currentState].join(', ')}`
            );
            this.emit('transition_error', { from: this.currentState, to: newState, error: error.message });
            throw error;
        }

        const previousState = this.currentState;
        this.currentState = newState;

        // Record state history
        this.stateHistory.push({
            from: previousState,
            to: newState,
            timestamp: new Date().toISOString(),
            context
        });

        // Update status file
        await this.updateStatusFile();

        // Emit state change event
        this.emit('state_changed', {
            from: previousState,
            to: newState,
            state: this.currentState,
            scenario: this.currentScenario,
            context
        });

        console.log(`[SimulationManager] State transition: ${previousState} -> ${newState}`);

        return { from: previousState, to: newState };
    }

    /**
     * Update status file with current state
     */
    async updateStatusFile() {
        const status = {
            state: this.currentState,
            scenario: this.currentScenario?.scenario || null,
            mode: this.currentScenario?.mode || null,
            town: this.currentScenario?.town || null,
            recording: this.recordingEnabled,
            updatedAt: new Date().toISOString()
        };

        // Add timestamps for specific states
        if (this.currentScenario) {
            if (this.currentScenario.loadedAt) {
                status.loadedAt = this.currentScenario.loadedAt;
            }
            if (this.currentScenario.startedAt) {
                status.startedAt = this.currentScenario.startedAt;
            }
        }

        try {
            if (this.currentState === this.states.STOPPED || this.currentState === this.states.IDLE) {
                // Remove file when stopped or idle
                if (fs.existsSync(this.statusFile)) {
                    fs.unlinkSync(this.statusFile);
                }
            } else {
                // Update or create file
                fs.writeFileSync(this.statusFile, JSON.stringify(status, null, 2));
            }
        } catch (error) {
            console.error('[SimulationManager] Error updating status file:', error);
        }
    }

    /**
     * Get PM2 description for a process by name
     */
    async getProcessDescription(name) {
        await this.connectPM2();
        return new Promise((resolve, reject) => {
            pm2.describe(name, (err, proc) => {
                if (err) return reject(err);
                if (!proc || !proc.length) return resolve(null);
                resolve(proc[0]);
            });
        });
    }

    /**
     * Restart orchestrator with updated environment (e.g., DATA_DUMP)
     */
    async restartOrchestratorWithEnv(envOverrides = {}) {
        await this.connectPM2();
        try {
            const proc = await this.getProcessDescription('orchestrator');
            if (!proc) {
                throw new Error('Orchestrator process not found');
            }

            const baseEnv = { ...(proc.pm2_env?.env || {}) };
            const mergedEnv = { ...baseEnv, ...envOverrides };

            // Stop then start with merged env to avoid CLI --env warnings
            await new Promise((resolve, reject) => {
                pm2.stop(proc.pm_id, (err) => (err ? reject(err) : resolve()));
            });

            const startOpts = {
                name: proc.name,
                script: proc.pm2_env.pm_exec_path,
                args: proc.pm2_env.args,
                interpreter: proc.pm2_env.exec_interpreter,
                cwd: proc.pm2_env.pm_cwd,
                watch: proc.pm2_env.watch,
                autorestart: proc.pm2_env.autorestart,
                env: mergedEnv
            };

            return await new Promise((resolve, reject) => {
                pm2.start(startOpts, (err, procRes) => {
                    if (err) return reject(err);
                    resolve(procRes);
                });
            });
        } finally {
            this.disconnectPM2();
        }
    }

    /**
     * Send a toggle_recording command to the orchestrator via WebSocket.
     * Returns a promise that resolves when the orchestrator acknowledges.
     */
    async sendRecordingToggleViaWS(enabled) {
        const wsPort = process.env.VEHICLE_WS_PORT || 8765;
        return new Promise((resolve, reject) => {
            const ws = new WebSocket(`ws://localhost:${wsPort}`);
            const timeout = setTimeout(() => {
                ws.close();
                reject(new Error('WebSocket toggle_recording timed out'));
            }, 5000);

            ws.on('open', () => {
                ws.send(JSON.stringify({ type: 'toggle_recording', enabled }));
            });

            ws.on('message', (data) => {
                try {
                    const msg = JSON.parse(data.toString());
                    if (msg.type === 'recording_status') {
                        clearTimeout(timeout);
                        ws.close();
                        resolve(msg);
                    }
                } catch (e) {
                    // ignore non-JSON messages
                }
            });

            ws.on('error', (err) => {
                clearTimeout(timeout);
                reject(err);
            });
        });
    }

    /**
     * Try sendRecordingToggleViaWS with up to `retries` attempts before giving up.
     * Does NOT fall back to restarting the orchestrator — that would kill a live simulation.
     */
    async sendRecordingToggleWithRetry(enabled, retries = 3, delayMs = 2000) {
        let lastErr;
        for (let i = 0; i < retries; i++) {
            try {
                return await this.sendRecordingToggleViaWS(enabled);
            } catch (err) {
                lastErr = err;
                console.warn(`[SimulationManager] WS toggle attempt ${i + 1}/${retries} failed: ${err.message}`);
                if (i < retries - 1) await new Promise(r => setTimeout(r, delayMs));
            }
        }
        throw lastErr;
    }

    /**
     * Start recording by sending a WebSocket command to the orchestrator.
     */
    async startRecording() {
        await this.startupPromise;

        if (this.currentState !== this.states.RUNNING) {
            throw new Error('Recording can only be started while simulation is running');
        }

        const result = await this.sendRecordingToggleWithRetry(true);
        this.recordingEnabled = true;
        await this.updateStatusFile();
        return {
            success: true,
            message: `Recording started for ${result.vehicles || '?'} vehicles`,
            vehicles: result.vehicles
        };
    }

    /**
     * Stop recording by sending a WebSocket command to the orchestrator.
     */
    async stopRecording() {
        await this.startupPromise;

        if (this.currentState !== this.states.RUNNING) {
            throw new Error('Recording can only be stopped while simulation is running');
        }

        await this.sendRecordingToggleWithRetry(false);
        this.recordingEnabled = false;
        await this.updateStatusFile();
        return { success: true, message: 'Recording stopped' };
    }

    /**
     * Connect to PM2
     */
    async connectPM2() {
        if (this.pm2Connected) {
            // Try to verify connection is still valid
            try {
                await new Promise((resolve, reject) => {
                    pm2.list((err) => {
                        if (err) {
                            // Connection lost, mark as disconnected
                            this.pm2Connected = false;
                            reject(err);
                        } else {
                            resolve();
                        }
                    });
                });
                return; // Connection is valid
            } catch (err) {
                // Connection invalid, will reconnect below
                this.pm2Connected = false;
            }
        }

        // Connect or reconnect
        return new Promise((resolve, reject) => {
            pm2.connect((err) => {
                if (err) {
                    this.pm2Connected = false;
                    reject(err);
                } else {
                    this.pm2Connected = true;
                    resolve();
                }
            });
        });
    }

    /**
     * Disconnect from PM2
     */
    disconnectPM2() {
        if (this.pm2Connected) {
            try {
                pm2.disconnect();
            } catch (err) {
                // Ignore disconnect errors
            }
            this.pm2Connected = false;
        }
    }

    /**
     * Safely execute a PM2 operation with error handling
     */
    async safePM2Operation(operation, ...args) {
        try {
            await this.connectPM2();
        } catch (connectErr) {
            throw new Error(`PM2 connection failed: ${connectErr.message}`);
        }

        return new Promise((resolve, reject) => {
            // Wrap in try-catch to catch synchronous errors (like null client)
            try {
                // PM2 operations use callbacks with (err) or (err, result) signature
                const callback = (err, result) => {
                    if (err) {
                        // Check if it's a null client error
                        const errMsg = err.message || String(err);
                        if (errMsg.includes('Cannot read properties of null') || 
                            errMsg.includes('reading \'call\'')) {
                            // Connection lost, mark as disconnected
                            this.pm2Connected = false;
                            reject(new Error('PM2 connection lost'));
                        } else {
                            reject(err);
                        }
                    } else {
                        resolve(result !== undefined ? result : true);
                    }
                };

                // Call the operation with args and callback
                operation(...args, callback);
            } catch (syncErr) {
                // Catch synchronous errors (like null client access)
                const errMsg = syncErr.message || String(syncErr);
                if (errMsg.includes('Cannot read properties of null') || 
                    errMsg.includes('reading \'call\'')) {
                    this.pm2Connected = false;
                    reject(new Error('PM2 connection lost'));
                } else {
                    reject(syncErr);
                }
            }
        });
    }

    /**
     * Get PM2 process list
     */
    async getProcessList() {
        try {
            return await this.safePM2Operation(pm2.list.bind(pm2));
        } catch (err) {
            // Return empty array if PM2 is unavailable
            console.warn('[SimulationManager] Failed to get PM2 process list:', err.message);
            return [];
        }
    }

    /**
     * Get service status
     */
    async getServiceStatus(serviceName) {
        const processes = await this.getProcessList();
        const service = processes.find(p => p.name === serviceName);
        return service ? service.pm2_env.status : 'stopped';
    }

    /**
     * Check if infrastructure is ready
     */
    async checkInfrastructureReady(scenarioConfig = null) {
        if (!fetch) {
            throw new Error('fetch is not available. Install node-fetch or use Node.js 18+');
        }

        const processes = await this.getProcessList();
        const sumo = processes.find(p => p.name === 'sumo-server');
        const artery = processes.find(p => p.name === 'artery');

        const sumoReady = sumo && sumo.pm2_env.status === 'online';

        // Check if artery is required
        let arteryRequired = false;
        if (scenarioConfig) {
            arteryRequired = scenarioConfig.artery?.enabled &&
                scenarioConfig.artery?.ini_path &&
                scenarioConfig.artery.ini_path !== '/path/to/your/artery/omnetpp.ini';
        }

        const arteryReady = arteryRequired
            ? (artery && artery.pm2_env.status === 'online')
            : true; // Not required

        // Infrastructure is ready when SUMO and Artery (if required) are online
        // Orchestrator is NOT required for "loaded" state - it starts when Play is pressed
        return {
            ready: sumoReady && arteryReady,
            sumo: sumoReady,
            artery: arteryReady,
            details: {
                sumo: sumo ? sumo.pm2_env.status : 'stopped',
                artery: artery ? artery.pm2_env.status : 'stopped'
            }
        };
    }

    /**
     * Load scenario (Step 1: Start infrastructure)
     */

    
    async loadScenario(scenarioName) {
        await this.startupPromise;
        this.activeStateOperations++;

        // If already running or loaded, stop first
        if (this.currentState === this.states.RUNNING || this.currentState === this.states.STARTING) {
            console.log(`[SimulationManager] Stopping current simulation before loading new scenario...`);
            try {
                await this.stopSimulation();
            } catch (err) {
                console.error('[SimulationManager] Error stopping before load:', err);
                // Continue anyway - try to stop sim services directly (keep frontend services alive)
                const simServices = ['orchestrator', 'artery', 'sumo-server', 'carla-headless'];
                for (const svc of simServices) {
                    try {
                        await execPromise(`pm2 delete ${svc}`);
                    } catch (_) {
                        // ignore missing
                    }
                }
                this.currentState = this.states.IDLE;
                this.currentScenario = null;
                await this.updateStatusFile();
            }
        }

        // Can only load from IDLE, STOPPED, LOADED (reload), or ERROR states
        if (this.currentState !== this.states.IDLE &&
            // this.currentState !== this.states.STOPPED &&
            // this.currentState !== this.states.LOADED &&
            this.currentState !== this.states.ERROR) {
            throw new Error(`Cannot load scenario in state: ${this.currentState}`);
        }

        if (!fetch) {
            throw new Error('fetch is not available. Install node-fetch or use Node.js 18+');
        }

        // Resolve scenario path - handle both relative and absolute paths
        let scenarioPath;
        if (path.isAbsolute(scenarioName)) {
            scenarioPath = scenarioName;
        } else {
            scenarioPath = path.join(__dirname, '../../scenarios', scenarioName);
        }

        if (!fs.existsSync(scenarioPath)) {
            throw new Error('Scenario not found');
        }

        // Load scenario config
        const content = fs.readFileSync(scenarioPath, 'utf8');
        const config = yaml.load(content);

        // Check if already loaded first
        const infrastructureStatus = await this.checkInfrastructureReady(config);
        if (infrastructureStatus.ready && this.currentState === this.states.LOADED) {
            // Already loaded, just update scenario info
            this.currentScenario = {
                scenario: scenarioName,
                mode: config.mode || 'sumo_cosim',
                town: config.carla?.town || 'Town01',
                config,
                loadedAt: new Date().toISOString()
            };
            return { success: true, message: 'Scenario already loaded' };
        }

        // Transition to loading
        await this.transition(this.states.LOADING, { scenario: scenarioName });

        try {
            // Check if infrastructure is ready (after transition to loading)
            if (infrastructureStatus.ready) {
                this.currentScenario = {
                    scenario: scenarioName,
                    mode: config.mode || 'sumo_cosim',
                    town: config.carla?.town || 'Town01',
                    config,
                    loadedAt: new Date().toISOString()
                };
                await this.transition(this.states.LOADED, { scenario: scenarioName });
                this.emit('scenario_loaded', {
                    scenario: this.currentScenario,
                    state: this.currentState
                });
                return { success: true, message: 'Scenario already loaded' };
            }

            // Stop all existing services (infrastructure + orchestrator)
            console.log('[SimulationManager] Stopping existing simulation services (keeping frontend/back)...');
            const allSimServices = ['orchestrator', 'artery', 'sumo-server', 'carla-headless', 'image-capture'];
            for (const svc of allSimServices) {
                try {
                    await execPromise(`pm2 delete ${svc}`);
                } catch (err) {
                    // ignore missing/stop errors
                }
            }
            await new Promise(resolve => setTimeout(resolve, 2000));

            // Start only infrastructure services (orchestrator starts when Play is pressed)
            const infrastructureServices = ['artery', 'sumo-server', 'carla-headless', 'image-capture'];
            const configFile = path.join(__dirname, '../../config/simulator.config.js');
            const scenarioRelPath = path.relative(path.join(__dirname, '../..'), scenarioPath);
            const cmd = `SIM_CONFIG=${scenarioRelPath} pm2 start ${configFile} --only "${infrastructureServices.join(',')}"`;

            console.log(`[SimulationManager] Starting infrastructure for scenario: ${scenarioName}`);
            const { stdout, stderr } = await execPromise(cmd, {
                cwd: path.join(__dirname, '../..')
            });

            // Wait for services to register
            await new Promise(resolve => setTimeout(resolve, 3000));

            // Wait for infrastructure services to be ready (SUMO, Artery if enabled)
            console.log('[SimulationManager] Waiting for infrastructure services to be ready...');
            await this.connectPM2();
            let infrastructureReady = false;
            for (let i = 0; i < 30; i++) { // Wait up to 30 seconds for infrastructure
                try {
                    const processes = await this.getProcessList();
                    const sumo = processes.find(p => p.name === 'sumo-server');
                    const artery = processes.find(p => p.name === 'artery');
                    
                    const sumoReady = sumo && sumo.pm2_env.status === 'online';
                    
                    // Check if artery is required
                    let arteryRequired = false;
                    if (config && config.artery) {
                        arteryRequired = config.artery.enabled &&
                            config.artery.ini_path &&
                            config.artery.ini_path !== '/path/to/your/artery/omnetpp.ini';
                    }
                    
                    const arteryReady = arteryRequired
                        ? (artery && artery.pm2_env.status === 'online')
                        : true; // Not required
                    
                    if (sumoReady && arteryReady) {
                        infrastructureReady = true;
                        break;
                    }
                } catch (err) {
                    // Continue waiting
                }
                await new Promise(resolve => setTimeout(resolve, 1000));
            }
            this.disconnectPM2();

            if (!infrastructureReady) {
                console.log('[SimulationManager] Infrastructure not fully ready, but continuing...');
            }

            // Store scenario info
            this.currentScenario = {
                scenario: scenarioName,
                mode: config.mode || 'sumo_cosim',
                town: config.carla?.town || config.world?.town || 'Town01',
                config,
                loadedAt: new Date().toISOString()
            };

            // Transition to loaded
            await this.transition(this.states.LOADED, { scenario: scenarioName });

            this.emit('scenario_loaded', {
                scenario: this.currentScenario,
                state: this.currentState
            });

            return {
                success: true,
                message: 'Scenario loaded successfully',
                scenario: this.currentScenario
            };

        } catch (error) {
            console.error('[SimulationManager] Error loading scenario:', error);
            await this.transition(this.states.ERROR, {
                scenario: scenarioName,
                error: error.message
            });
            throw error;
        } finally {
            this.activeStateOperations = Math.max(0, this.activeStateOperations - 1);
        }
    }

    /**
     * Start simulation (Step 2: Verify orchestrator is running)
     * Note: Orchestrator already starts in loadScenario, this just verifies it's running
     */
    async startSimulation() {
        await this.startupPromise;
        this.activeStateOperations++;

        try {
            if (this.currentState === this.states.LOADING) {
                console.log('[SimulationManager] Start requested while loading; waiting for loaded state...');
                for (let i = 0; i < 60 && this.currentState === this.states.LOADING; i++) {
                    await new Promise(resolve => setTimeout(resolve, 1000));
                }
            }

            // If already running, just return success
            if (this.currentState === this.states.RUNNING) {
                return { success: true, message: 'Simulation already running' };
            }

            if (this.currentState === this.states.ERROR) {
                // Allow retry after a failed start — reset to LOADED so the transition is valid
                console.warn('[SimulationManager] Recovering from ERROR state to retry start');
                this.currentState = this.states.LOADED;
            }

            if (this.currentState !== this.states.LOADED) {
                throw new Error(`Cannot start simulation in state: ${this.currentState}. Must be in 'loaded' state.`);
            }

            // Verify infrastructure is ready (SUMO, Artery - orchestrator not required yet)
            const infrastructureStatus = await this.checkInfrastructureReady(this.currentScenario?.config);
            if (!infrastructureStatus.ready) {
                throw new Error(
                    `Infrastructure not ready. ` +
                    `SUMO: ${infrastructureStatus.details.sumo}, ` +
                    `Artery: ${infrastructureStatus.details.artery}`
                );
            }

            // Check if orchestrator is already running
            await this.connectPM2();
            const processes = await this.getProcessList();
            const orchestratorProcess = processes.find(p => p.name === 'orchestrator');
            const orchestratorStatus = orchestratorProcess ? orchestratorProcess.pm2_env.status : 'stopped';
            
            if (orchestratorStatus === 'online') {
                // Only transition if not already in RUNNING state
                if (this.currentState !== this.states.RUNNING) {
                    await this.transition(this.states.RUNNING);
                }
                this.disconnectPM2();
                return { success: true, message: 'Simulation already running' };
            }

            // Transition to starting
            await this.transition(this.states.STARTING);

            try {
                // CARLA's RPC port can come up before the simulator is ready to
                // handle load_world(). Give it a short warm-up window before
                // starting the orchestrator, which immediately loads the town.
                const carlaWarmupSeconds = Number(
                    this.currentScenario?.config?.carla?.startup_grace_seconds ?? 8
                );
                if (carlaWarmupSeconds > 0) {
                    console.log(`[SimulationManager] Waiting ${carlaWarmupSeconds}s for CARLA to finish initializing...`);
                    await new Promise(resolve => setTimeout(resolve, carlaWarmupSeconds * 1000));
                }

                // Ensure orchestrator service is defined in PM2 (may need to add it first)
                if (!orchestratorProcess) {
                    console.log('[SimulationManager] Orchestrator not found in PM2, adding it...');
                    // Load orchestrator service from config
                    const configFile = path.join(__dirname, '../../config/simulator.config.js');
                    const scenarioPath = this.currentScenario?.scenario;
                    if (scenarioPath) {
                        const scenarioFullPath = path.join(__dirname, '../../scenarios', scenarioPath);
                        const scenarioRelPath = path.relative(path.join(__dirname, '../..'), scenarioFullPath);
                        const cmd = `SIM_CONFIG=${scenarioRelPath} pm2 start ${configFile} --only orchestrator`;
                        console.log(`[SimulationManager] Command: ${cmd}`);
                        const { stdout, stderr } = await execPromise(cmd, {
                            cwd: path.join(__dirname, '../..')
                        });
                        // Wait for service to register
                        await new Promise(resolve => setTimeout(resolve, 3000));
                    } else {
                        this.disconnectPM2();
                        throw new Error('Cannot start orchestrator: scenario path not available');
                    }
                } else {
                    // Orchestrator exists but not running, start it
                    console.log('[SimulationManager] Starting orchestrator...');
                    try {
                        await this.safePM2Operation(pm2.start.bind(pm2), 'orchestrator');
                    } catch (err) {
                        this.disconnectPM2();
                        throw new Error(`Failed to start orchestrator: ${err.message}`);
                    }
                }

                // Wait a bit for service to start
                await new Promise(resolve => setTimeout(resolve, 2000));

                // Verify it started
                const newStatus = await this.getServiceStatus('orchestrator');
                if (newStatus !== 'online' && newStatus !== 'launching') {
                    this.disconnectPM2();
                    throw new Error(`Failed to start orchestrator. Status: ${newStatus}`);
                }

                // Update scenario with start time
                if (this.currentScenario) {
                    this.currentScenario.startedAt = new Date().toISOString();
                }

                // Transition to running (only if not already in RUNNING state)
                // State might have been updated by reactive status check while we were starting
                if (this.currentState === this.states.RUNNING) {
                    // Already running, likely updated by reactive check
                    console.log('[SimulationManager] State already RUNNING, skipping transition');
                } else if (this.currentState === this.states.STARTING) {
                    // Still in STARTING state, transition to RUNNING
                    await this.transition(this.states.RUNNING);
                } else {
                    // Unexpected state, log warning but try to transition anyway
                    console.warn(`[SimulationManager] Unexpected state ${this.currentState} when trying to transition to RUNNING`);
                    if (this.canTransition(this.states.RUNNING)) {
                        await this.transition(this.states.RUNNING);
                    }
                }

                this.emit('simulation_started', {
                    scenario: this.currentScenario,
                    state: this.currentState
                });

                return {
                    success: true,
                    message: 'Simulation started successfully'
                };

            } catch (error) {
                console.error('[SimulationManager] Error starting simulation:', error);
                await this.transition(this.states.ERROR, { error: error.message });
                throw error;
            } finally {
                this.disconnectPM2();
            }
        } finally {
            this.activeStateOperations = Math.max(0, this.activeStateOperations - 1);
        }
    }

    /**
     * Stop simulation (stops all services and returns to idle)
     */
    async stopSimulation() {
        await this.startupPromise;
        this.activeStateOperations++;

        try {
            // If already idle or stopped, just return
            if (this.currentState === this.states.IDLE || this.currentState === this.states.STOPPED) {
                return { success: true, message: 'Simulation already stopped' };
            }

            // Can stop from any state except already stopped/idle
            if (this.currentState === this.states.STOPPING) {
                // Already stopping — wait for it to finish rather than throwing
                console.log('[SimulationManager] Already stopping, waiting…');
                for (let i = 0; i < 30 && this.currentState === this.states.STOPPING; i++) {
                    await new Promise(r => setTimeout(r, 1000));
                }
                return { success: true, message: 'Simulation stopped' };
            } else if (!this.canTransition(this.states.STOPPING)) {
                // Force transition from error/loaded/loading states
                if (this.currentState === this.states.ERROR ||
                    this.currentState === this.states.LOADED ||
                    this.currentState === this.states.LOADING ||
                    this.currentState === this.states.STARTING) {
                    this.currentState = this.states.STOPPING;
                } else {
                    throw new Error(`Cannot stop simulation in state: ${this.currentState}`);
                }
            } else {
                await this.transition(this.states.STOPPING);
            }

            try {
                console.log('[SimulationManager] Stopping all services...');

                // Stop only simulation services. The GUI/API must stay alive so this
                // request can complete and the user can start another scenario.
                const services = ['orchestrator', 'artery', 'sumo-server', 'carla-headless', 'image-capture'];

                try {
                    await this.connectPM2();

                    for (const serviceName of services) {
                        try {
                            await this.safePM2Operation(pm2.stop.bind(pm2), serviceName);
                            console.log(`[SimulationManager] Stopped ${serviceName}`);
                            await new Promise(resolve => setTimeout(resolve, 500));
                        } catch (err) {
                            if (err.message.includes('not found') || err.message.includes('PM2 connection')) {
                                console.log(`[SimulationManager] Service ${serviceName} not running or PM2 connection issue`);
                            } else {
                                console.log(`[SimulationManager] Error stopping ${serviceName}: ${err.message}`);
                            }
                        }
                    }
                } catch (pm2Err) {
                    console.warn('[SimulationManager] PM2 connection error, trying alternative method:', pm2Err.message);
                    // Try using exec as fallback to stop all services
                    try {
                        await execPromise('pm2 stop all || true');
                        await new Promise(resolve => setTimeout(resolve, 1000));
                    } catch (execErr) {
                        console.warn('[SimulationManager] Failed to stop services via exec:', execErr.message);
                    }
                } finally {
                    this.disconnectPM2();
                }

                // Clear scenario
                this.currentScenario = null;
                this.recordingEnabled = false;

                // Transition to idle (initial state)
                await this.transition(this.states.IDLE);

                this.emit('simulation_stopped', {
                    state: this.currentState
                });

                return {
                    success: true,
                    message: 'All services stopped successfully'
                };

            } catch (error) {
                console.error('[SimulationManager] Error stopping simulation:', error);
                await this.transition(this.states.ERROR, { error: error.message });
                throw error;
            }
        } finally {
            this.activeStateOperations = Math.max(0, this.activeStateOperations - 1);
        }
    }

    /**
     * Stop all services (full shutdown) - alias for stopSimulation
     */
    async stopAll() {
        // stopSimulation now stops all services and returns to idle
        return this.stopSimulation();
    }

    /**
     * Get current status (reactive check from PM2)
     */
    async getStatus() {
        await this.startupPromise;

        try {
            const processes = await this.getProcessList();
            const orchestrator = processes.find(p => p.name === 'orchestrator');
            const carla = processes.find(p => p.name === 'carla-headless');
            const sumo = processes.find(p => p.name === 'sumo-server');
            const artery = processes.find(p => p.name === 'artery');

            // Sync recording flag from status file (set by startRecording/stopRecording).
            // Do NOT override from DATA_DUMP env — WS-toggled recording doesn't change that var.

            // Determine state from PM2 (for reactive updates)
            let detectedState = this.currentState;

            // First check if all services are gone (stopped)
            const allServicesGone = !carla && !sumo && !artery && !orchestrator;
            if (allServicesGone && processes.length === 0) {
                // All services deleted, force transition to IDLE
                detectedState = this.states.IDLE;
            } else if (orchestrator && orchestrator.pm2_env.status === 'online') {
                detectedState = this.states.RUNNING;
            } else if (orchestrator && orchestrator.pm2_env.status === 'launching') {
                detectedState = this.states.STARTING;
            } else if (orchestrator && orchestrator.pm2_env.status === 'stopping') {
                detectedState = this.states.STOPPING;
            } else {
                // Check infrastructure
                try {
                    const infraStatus = await this.checkInfrastructureReady(this.currentScenario?.config);
                    if (infraStatus.ready) {
                        detectedState = this.states.LOADED;
                    } else if (orchestrator || sumo) {
                        // Only set to LOADING if current state is STOPPED or LOADING
                        // Don't override STOPPING state
                        if (this.currentState === this.states.STOPPED || this.currentState === this.states.LOADING) {
                            detectedState = this.states.LOADING;
                        } else if (this.currentState === this.states.STOPPING) {
                            detectedState = this.states.STOPPING;
                        }
                    } else {
                        detectedState = this.states.IDLE;
                    }
                } catch (err) {
                    // If fetch not available or other error, check if any services exist
                    if (orchestrator || sumo || carla) {
                        // Only set to LOADING if current state is IDLE, STOPPED, or LOADING
                        if (this.currentState === this.states.IDLE || this.currentState === this.states.STOPPED || this.currentState === this.states.LOADING) {
                            detectedState = this.states.LOADING;
                        } else if (this.currentState === this.states.STOPPING) {
                            detectedState = this.states.STOPPING;
                        }
                    } else {
                        detectedState = this.states.IDLE;
                    }
                }
            }

            // Sync state if different (for reactive updates)
            // Force transition to IDLE if all services are gone, even if invalid transition
            const transitionsAllowed = this.activeStateOperations === 0;
            if (transitionsAllowed) {
                if (allServicesGone && processes.length === 0) {
                    if (this.currentState !== this.states.IDLE) {
                        console.log('[SimulationManager] All services deleted, forcing transition to IDLE');
                        await this.transition(this.states.IDLE, { source: 'force_stop_all_services_deleted' });
                        this.currentScenario = null;
                    }
                } else if (detectedState !== this.currentState && this.canTransition(detectedState)) {
                    try {
                        await this.transition(detectedState, { source: 'reactive_check' });
                    } catch (err) {
                        // Invalid transition, keep current state
                    }
                }
            }

            const allServices = processes.map(proc => ({
                name: proc.name,
                status: proc.pm2_env.status,
                pid: proc.pid,
                uptime: proc.pm2_env.pm_uptime
            }));

            // Load scenario from file if available
            let scenario = null;
            if (fs.existsSync(this.statusFile)) {
                scenario = JSON.parse(fs.readFileSync(this.statusFile, 'utf8'));
            }

            return {
                success: true,
                state: this.currentState,
                detectedState, // State detected from PM2
                scenario: scenario?.scenario || this.currentScenario?.scenario || null,
                recording: this.recordingEnabled,
                services: allServices,
                details: {
                    orchestrator: orchestrator ? orchestrator.pm2_env.status : 'stopped',
                    carla: carla ? carla.pm2_env.status : 'stopped',
                    sumo: sumo ? sumo.pm2_env.status : 'stopped',
                    artery: artery ? artery.pm2_env.status : 'stopped'
                }
            };

        } catch (error) {
            return {
                success: false,
                error: error.message,
                state: this.currentState
            };
        }
    }
}

module.exports = SimulationManager;
