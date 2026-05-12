const express = require('express');
const cors = require('cors');
const WebSocket = require('ws');
const http = require('http');
const net = require('net');
const pm2 = require('pm2');
const fs = require('fs');
const yaml = require('js-yaml');
const path = require('path');
const os = require('os');
const { exec, spawn } = require('child_process');
const util = require('util');
const multer = require('multer');

const execPromise = util.promisify(exec);
const PROJECT_ROOT = path.resolve(__dirname, '../..');

function loadGlobalConfig() {
    const globalConfigPath = path.join(PROJECT_ROOT, 'config', 'global.config.yaml');
    if (!fs.existsSync(globalConfigPath)) {
        return {};
    }

    try {
        return yaml.load(fs.readFileSync(globalConfigPath, 'utf8')) || {};
    } catch (error) {
        console.warn(`[Server] Could not read global config: ${error.message}`);
        return {};
    }
}

const globalConfig = loadGlobalConfig();

// Import SimulationManager
const SimulationManager = require('./simulation-manager');

const app = express();
const server = http.createServer(app);
const wss = new WebSocket.Server({ 
    server,
    path: undefined // Accept all paths, we'll handle routing manually
});

const PORT = process.env.PORT || 3001;
const HOST = process.env.HOST || process.env.BACKEND_HOST || '0.0.0.0';

// Create singleton SimulationManager instance
const simulationManager = new SimulationManager({
    statusFile: path.join(PROJECT_ROOT, '.scenario_status.json'),
    configLauncherUrl: 'http://127.0.0.1:5001'
});

// Forward SimulationManager events to WebSocket clients
simulationManager.on('state_changed', (data) => {
    broadcast({ type: 'simulation_state_changed', ...data });
});

simulationManager.on('scenario_loaded', (data) => {
    broadcast({ type: 'simulation_loaded', ...data });
});

simulationManager.on('simulation_started', (data) => {
    broadcast({ type: 'simulation_started', ...data });
});

simulationManager.on('simulation_stopped', (data) => {
    broadcast({ type: 'simulation_stopped', ...data });
});

simulationManager.on('transition_error', (data) => {
    console.error('[SimulationManager] Transition error:', data);
    broadcast({ type: 'simulation_error', ...data });
});

app.use(cors());
app.use(express.json());

// Serve static files from scenarios directory (for thumbnails)
app.use('/scenarios', express.static(path.join(__dirname, '../../scenarios')));

// Configure multer for file uploads
const upload = multer({
  dest: path.join(__dirname, '../../scenarios/'),
  fileFilter: (req, file, cb) => {
    // Only accept YAML files
    if (file.mimetype === 'application/x-yaml' || 
        file.mimetype === 'text/yaml' ||
        file.originalname.endsWith('.yaml') ||
        file.originalname.endsWith('.yml')) {
      cb(null, true);
    } else {
      cb(new Error('Only YAML files are allowed'));
    }
  },
  limits: {
    fileSize: 10 * 1024 * 1024 // 10MB limit
  }
});

// WebSocket connections
const clients = new Set();
const VEHICLE_WS_PORT = process.env.VEHICLE_WS_PORT || 8765;

wss.on('connection', (ws, req) => {
    console.log('[WebSocket] New connection attempt:', req.url);
    
    // Check if this is a vehicle map connection
    // Handle both /vehicles and /vehicles?query=params
    const urlPath = req.url.split('?')[0];
    if (urlPath === '/vehicles') {
        console.log('[Vehicles WS] ✅ Frontend client connected from:', req.url);
        console.log('[Vehicles WS] Proxying to Python WebSocket server on port', VEHICLE_WS_PORT);

        // Connect to Python WebSocket server
        let pythonWs;
        try {
            pythonWs = new WebSocket(`ws://localhost:${VEHICLE_WS_PORT}`);

            pythonWs.on('open', () => {
                console.log('[Vehicles WS] Connected to Python simulation');
            });

            pythonWs.on('message', (data) => {
                // Forward data from Python to frontend
                if (ws.readyState === WebSocket.OPEN) {
                    ws.send(data);
                }
            });

            pythonWs.on('close', () => {
                console.log('[Vehicles WS] Python connection closed');
                if (ws.readyState === WebSocket.OPEN) {
                    ws.close();
                }
            });

            pythonWs.on('error', (err) => {
                // Only log connection refused errors once to reduce noise
                if (err.message.includes('ECONNREFUSED')) {
                    // Log once, then suppress repeated connection attempts
                    if (!ws._connectionErrorLogged) {
                        console.log(`[Vehicles WS] Python WebSocket server not available on port ${VEHICLE_WS_PORT}. This is normal when the simulation is not running.`);
                        ws._connectionErrorLogged = true;
                    }
                } else {
                    console.error('[Vehicles WS] Python connection error:', err.message);
                }
                if (ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({
                        type: 'error',
                        message: `Failed to connect to Python WebSocket server on port ${VEHICLE_WS_PORT}. Make sure the simulation is running.`
                    }));
                }
            });

            ws.on('close', () => {
                console.log('[Vehicles WS] Frontend client disconnected');
                if (pythonWs && pythonWs.readyState === WebSocket.OPEN) {
                    pythonWs.close();
                }
            });

            ws.on('error', (err) => {
                console.error('[Vehicles WS] Frontend connection error:', err.message);
            });

        } catch (error) {
            console.error('[Vehicles WS] Failed to connect to Python:', error.message);
            if (ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({
                    type: 'error',
                    message: `Failed to connect to Python WebSocket server: ${error.message}. Make sure the simulation is running and the WebSocket server is enabled.`
                }));
            }
            setTimeout(() => ws.close(), 1000);
        }

        return; // Don't add to general clients set
    }

    // Regular status update connection
    console.log('Client connected');
    clients.add(ws);

    ws.on('close', () => {
        console.log('Client disconnected');
        clients.delete(ws);
    });
});

// Broadcast to all connected clients
function broadcast(data) {
    const message = JSON.stringify(data);
    clients.forEach((client) => {
        if (client.readyState === WebSocket.OPEN) {
            client.send(message);
        }
    });
}

// Connect to PM2
function connectPM2() {
    return new Promise((resolve, reject) => {
        pm2.connect((err) => {
            if (err) {
                console.error('PM2 connection error:', err);
                reject(err);
            } else {
                console.log('Connected to PM2');
                resolve();
            }
        });
    });
}

// Get PM2 process list
function getProcessList() {
    return new Promise((resolve, reject) => {
        pm2.list((err, processes) => {
            if (err) reject(err);
            else resolve(processes);
        });
    });
}

// API Routes

// Get all services status
app.get('/api/services', async (req, res) => {
    try {
        await connectPM2();
        const processes = await getProcessList();

        const services = processes.map(proc => ({
            name: proc.name,
            pm_id: proc.pm_id,
            status: proc.pm2_env.status,
            pid: proc.pid,
            cpu: proc.monit.cpu,
            memory: proc.monit.memory,
            uptime: proc.pm2_env.pm_uptime,
            restarts: proc.pm2_env.restart_time,
            script: proc.pm2_env.pm_exec_path,
            args: proc.pm2_env.args
        }));

        pm2.disconnect();
        res.json({ success: true, services });
    } catch (error) {
        res.status(500).json({ success: false, error: error.message });
    }
});

// Start a service
app.post('/api/services/:name/start', async (req, res) => {
    const { name } = req.params;
    try {
        await connectPM2();

        pm2.start(name, (err) => {
            if (err) {
                pm2.disconnect();
                return res.status(500).json({ success: false, error: err.message });
            }

            broadcast({ type: 'service_started', service: name });
            pm2.disconnect();
            res.json({ success: true, message: `Service ${name} started` });
        });
    } catch (error) {
        res.status(500).json({ success: false, error: error.message });
    }
});

// Stop a service
app.post('/api/services/:name/stop', async (req, res) => {
    const { name } = req.params;
    try {
        await connectPM2();

        pm2.stop(name, (err) => {
            if (err) {
                pm2.disconnect();
                return res.status(500).json({ success: false, error: err.message });
            }

            broadcast({ type: 'service_stopped', service: name });
            pm2.disconnect();
            res.json({ success: true, message: `Service ${name} stopped` });
        });
    } catch (error) {
        res.status(500).json({ success: false, error: error.message });
    }
});

// Restart a service
app.post('/api/services/:name/restart', async (req, res) => {
    const { name } = req.params;
    try {
        await connectPM2();

        pm2.restart(name, (err) => {
            if (err) {
                pm2.disconnect();
                return res.status(500).json({ success: false, error: err.message });
            }

            broadcast({ type: 'service_restarted', service: name });
            pm2.disconnect();
            res.json({ success: true, message: `Service ${name} restarted` });
        });
    } catch (error) {
        res.status(500).json({ success: false, error: error.message });
    }
});

// Get service logs
app.get('/api/services/:name/logs', async (req, res) => {
    const { name } = req.params;
    const lines = parseInt(req.query.lines) || 100;

    try {
        const { stdout, stderr } = await execPromise(`pm2 logs ${name} --lines ${lines} --nostream --raw`);
        res.json({ success: true, logs: stdout || stderr });
    } catch (error) {
        res.status(500).json({ success: false, error: error.message });
    }
});

// Start all services (using ecosystem config)
app.post('/api/services/start-all', async (req, res) => {
    try {
        const { scenario } = req.body;
        const configPath = path.join(__dirname, '../../config/simulator.config.js');

        // Build command with optional scenario
        let command = `pm2 start ${configPath}`;
        if (scenario) {
            const scenarioPath = path.join(__dirname, '../../scenarios', scenario);
            command = `SIM_CONFIG=${scenarioPath} ${command}`;
        }

        const { stdout, stderr } = await execPromise(command);

        broadcast({ type: 'all_services_started', scenario });
        res.json({ success: true, message: 'All services started', output: stdout, scenario });
    } catch (error) {
        res.status(500).json({ success: false, error: error.message });
    }
});

// Stop all services
app.post('/api/services/stop-all', async (req, res) => {
    try {
        // Use SimulationManager to stop all services and return to idle
        const result = await simulationManager.stopSimulation();
        broadcast({ type: 'all_services_stopped' });
        res.json(result);
    } catch (error) {
        console.error('Error stopping all services:', error);
        res.status(500).json({ success: false, error: error.message });
    }
});

// Get scenarios
app.get('/api/scenarios', (req, res) => {
    const scenariosDir = path.join(__dirname, '../../scenarios');

    try {
        const files = fs.readdirSync(scenariosDir)
            .filter(file => file.endsWith('.yaml') || file.endsWith('.yml') || file.endsWith('.yam'));

        res.json({ success: true, scenarios: files });
    } catch (error) {
        res.status(500).json({ success: false, error: error.message });
    }
});

// Get current running scenario
app.get('/api/scenarios/current', (req, res) => {
    const statusFile = path.join(__dirname, '../../.scenario_status.json');

    try {
        if (fs.existsSync(statusFile)) {
            const status = JSON.parse(fs.readFileSync(statusFile, 'utf8'));
            res.json({ success: true, scenario: status });
        } else {
            res.json({ success: true, scenario: null });
        }
    } catch (error) {
        res.status(500).json({ success: false, error: error.message });
    }
});

// Get scenario content
app.get('/api/scenarios/:name', (req, res) => {
    const { name } = req.params;
    const scenarioPath = path.join(__dirname, '../../scenarios', name);

    try {
        if (!fs.existsSync(scenarioPath)) {
            return res.status(404).json({ success: false, error: 'Scenario not found' });
        }

        const content = fs.readFileSync(scenarioPath, 'utf8');
        const config = yaml.load(content);

        res.json({ success: true, config, raw: content });
    } catch (error) {
        res.status(500).json({ success: false, error: error.message });
    }
});

// Upload scenario file
app.post('/api/scenarios/upload', upload.single('scenario'), (req, res) => {
    try {
        if (!req.file) {
            return res.status(400).json({ success: false, error: 'No file uploaded' });
        }

        const scenariosDir = path.join(__dirname, '../../scenarios');
        const originalName = req.file.originalname || `scenario_${Date.now()}.yaml`;
        const finalPath = path.join(scenariosDir, originalName);

        // Move uploaded file to final location with original name
        fs.renameSync(req.file.path, finalPath);

        // Validate it's a valid YAML file
        try {
            const content = fs.readFileSync(finalPath, 'utf8');
            yaml.load(content); // Validate YAML syntax
        } catch (yamlError) {
            // Delete invalid file
            fs.unlinkSync(finalPath);
            return res.status(400).json({ 
                success: false, 
                error: 'Invalid YAML file: ' + yamlError.message 
            });
        }

        res.json({ 
            success: true, 
            message: 'Scenario uploaded successfully',
            filename: originalName
        });
    } catch (error) {
        console.error('Error uploading scenario:', error);
        // Clean up uploaded file if it exists
        if (req.file && fs.existsSync(req.file.path)) {
            try {
                fs.unlinkSync(req.file.path);
            } catch (e) {
                // Ignore cleanup errors
            }
        }
        res.status(500).json({ success: false, error: error.message });
    }
});

// Carla Image Capture API (Proxy to Python service)
const CARLA_IMAGE_SERVICE = 'http://localhost:5000';

// Get list of vehicles in Carla
app.get('/api/carla/vehicles', async (req, res) => {
    try {
        const response = await fetch(`${CARLA_IMAGE_SERVICE}/vehicles`);
        const data = await response.json();
        res.json(data);
    } catch (error) {
        res.status(503).json({ success: false, error: 'Image capture service not available' });
    }
});

// Capture image from spectator camera
app.get('/api/carla/capture', async (req, res) => {
    try {
        const { width, height, fov, quality } = req.query;
        const params = new URLSearchParams();
        if (width) params.append('width', width);
        if (height) params.append('height', height);
        if (fov) params.append('fov', fov);
        if (quality) params.append('quality', quality);

        const response = await fetch(`${CARLA_IMAGE_SERVICE}/capture?${params.toString()}`);

        if (!response.ok) {
            const error = await response.json();
            return res.status(response.status).json(error);
        }

        // Set content type and stream the image
        res.setHeader('Content-Type', 'image/jpeg');
        const buffer = await response.arrayBuffer();
        res.send(Buffer.from(buffer));
    } catch (error) {
        console.error('Image capture error:', error);
        res.status(503).json({ success: false, error: 'Image capture service not available' });
    }
});

// Capture image from vehicle camera
app.get('/api/carla/capture/vehicle/:id', async (req, res) => {
    try {
        const { id } = req.params;
        const { width, height, fov, quality } = req.query;
        const params = new URLSearchParams();
        if (width) params.append('width', width);
        if (height) params.append('height', height);
        if (fov) params.append('fov', fov);
        if (quality) params.append('quality', quality);

        const response = await fetch(`${CARLA_IMAGE_SERVICE}/capture/vehicle/${id}?${params.toString()}`);

        if (!response.ok) {
            const error = await response.json();
            return res.status(response.status).json(error);
        }

        // Set content type and stream the image
        res.setHeader('Content-Type', 'image/jpeg');
        const buffer = await response.arrayBuffer();
        res.send(Buffer.from(buffer));
    } catch (error) {
        console.error('Vehicle image capture error:', error);
        res.status(503).json({ success: false, error: 'Image capture service not available' });
    }
});

// Check image capture service health
app.get('/api/carla/health', async (req, res) => {
    try {
        const response = await fetch(`${CARLA_IMAGE_SERVICE}/health`);
        const data = await response.json();
        res.json(data);
    } catch (error) {
        res.status(503).json({ status: 'unavailable', connected: false });
    }
});

// Stream video from spectator camera
app.get('/api/carla/stream', async (req, res) => {
    try {
        const { width, height, fov, fps } = req.query;
        const params = new URLSearchParams();
        if (width) params.append('width', width);
        if (height) params.append('height', height);
        if (fov) params.append('fov', fov);
        if (fps) params.append('fps', fps);

        const response = await fetch(`${CARLA_IMAGE_SERVICE}/stream?${params.toString()}`);

        if (!response.ok) {
            const error = await response.json();
            return res.status(response.status).json(error);
        }

        // Stream the video
        res.setHeader('Content-Type', 'multipart/x-mixed-replace; boundary=frame');
        const { Readable } = require('stream');
        Readable.fromWeb(response.body).pipe(res);
    } catch (error) {
        console.error('Video stream error:', error);
        res.status(503).json({ success: false, error: 'Video stream service not available' });
    }
});

// Stream video from vehicle camera
app.get('/api/carla/stream/vehicle/:id', async (req, res) => {
    try {
        const { id } = req.params;
        const { width, height, fov, fps } = req.query;
        const params = new URLSearchParams();
        if (width) params.append('width', width);
        if (height) params.append('height', height);
        if (fov) params.append('fov', fov);
        if (fps) params.append('fps', fps);

        const response = await fetch(`${CARLA_IMAGE_SERVICE}/stream/vehicle/${id}?${params.toString()}`);

        if (!response.ok) {
            const error = await response.json();
            return res.status(response.status).json(error);
        }

        // Stream the video
        res.setHeader('Content-Type', 'multipart/x-mixed-replace; boundary=frame');
        const { Readable } = require('stream');
        Readable.fromWeb(response.body).pipe(res);
    } catch (error) {
        console.error('Vehicle video stream error:', error);
        res.status(503).json({ success: false, error: 'Video stream service not available' });
    }
});

// System info
app.get('/api/system', async (req, res) => {
    try {
        const { stdout: pm2Version } = await execPromise('pm2 --version');
        const { stdout: nodeVersion } = await execPromise('node --version');

        res.json({
            success: true,
            system: {
                pm2Version: pm2Version.trim(),
                nodeVersion: nodeVersion.trim(),
                platform: process.platform,
                uptime: process.uptime()
            }
        });
    } catch (error) {
        res.status(500).json({ success: false, error: error.message });
    }
});

// Health check
// Scenario Management API



// Start a scenario
app.post('/api/scenarios/start', async (req, res) => {
    const { scenario } = req.body;

    if (!scenario) {
        return res.status(400).json({ success: false, error: 'Scenario name is required' });
    }

    const scenarioPath = path.join(__dirname, '../../scenarios', scenario);

    try {
        // Check if scenario exists
        if (!fs.existsSync(scenarioPath)) {
            return res.status(404).json({ success: false, error: 'Scenario not found' });
        }

        // Load scenario config to get details
        const content = fs.readFileSync(scenarioPath, 'utf8');
        const config = yaml.load(content);

        // Stop all existing services first
        console.log('Stopping existing services...');
        await execPromise('pm2 delete all || true');

        // Wait a bit for cleanup
        await new Promise(resolve => setTimeout(resolve, 2000));

        // Start services with the selected scenario
        const configFile = path.join(__dirname, '../../config/simulator.config.js');
        const cmd = `SIM_CONFIG=scenarios/${scenario} pm2 start ${configFile}`;

        console.log(`Starting scenario: ${scenario}`);
        console.log(`Command: ${cmd}`);

        const { stdout, stderr } = await execPromise(cmd, {
            cwd: path.join(__dirname, '../..')
        });

        // Save current scenario status
        const statusFile = path.join(__dirname, '../../.scenario_status.json');
        const status = {
            scenario: scenario,
            mode: config.mode || 'sumo_cosim',
            town: config.carla?.town || 'Unknown',
            startedAt: new Date().toISOString()
        };
        fs.writeFileSync(statusFile, JSON.stringify(status, null, 2));

        res.json({
            success: true,
            message: 'Scenario started successfully',
            scenario: status,
            output: stdout
        });
    } catch (error) {
        console.error('Error starting scenario:', error);
        res.status(500).json({
            success: false,
            error: error.message,
            stderr: error.stderr
        });
    }
});

// Stop current scenario
app.post('/api/scenarios/stop', async (req, res) => {
    try {
        console.log('Stopping all services...');

        // Use SimulationManager to stop properly
        try {
            await simulationManager.stopSimulation();
        } catch (err) {
            // If SimulationManager fails, try direct PM2 delete
            console.log('SimulationManager stop failed, using direct PM2 delete:', err.message);
            await execPromise('pm2 delete all || true');

            // Force state update
            simulationManager.currentState = simulationManager.states.STOPPED;
            simulationManager.currentScenario = null;
            await simulationManager.updateStatusFile();
        }

        res.json({ success: true, message: 'All services stopped' });
    } catch (error) {
        console.error('Error stopping scenario:', error);
        res.status(500).json({ success: false, error: error.message });
    }
});

// Simulation Control API (for player controls)

// Note: Config-launcher service has been replaced by orchestrator
// The orchestrator handles everything including scenario configuration

// Get simulation status
app.get('/api/simulation/status', async (req, res) => {
    try {
        const status = await simulationManager.getStatus();
        res.json(status);
    } catch (error) {
        res.status(500).json({ success: false, error: error.message });
    }
});

// Load simulation scenario (Step 1: Start orchestrator, sumo, artery)
app.post('/api/simulation/load', async (req, res) => {
    const { scenario } = req.body;

    if (!scenario) {
        return res.status(400).json({ success: false, error: 'Scenario name is required' });
    }

    try {
        const result = await simulationManager.loadScenario(scenario);
        res.json(result);
    } catch (error) {
        console.error('Error loading scenario:', error);
        res.status(500).json({
            success: false,
            error: error.message
        });
    }
});

// Start simulation (Step 2: Verify orchestrator is running)
app.post('/api/simulation/start', async (req, res) => {
    try {
        const result = await simulationManager.startSimulation();
        res.json(result);
    } catch (error) {
        console.error('Error starting simulation:', error);
        res.status(500).json({
            success: false,
            error: error.message
        });
    }
});

// Stop simulation
app.post('/api/simulation/stop', async (req, res) => {
    try {
        const result = await simulationManager.stopSimulation();
        res.json(result);
    } catch (error) {
        console.error('Error stopping simulation:', error);
        res.status(500).json({ success: false, error: error.message });
    }
});

// Restart simulation
app.post('/api/simulation/restart', async (req, res) => {
    const { scenario } = req.body;

    try {
        // Get current scenario if not provided
        let scenarioToUse = scenario;
        if (!scenarioToUse) {
            const statusFile = path.join(__dirname, '../../.scenario_status.json');
            if (fs.existsSync(statusFile)) {
                const status = JSON.parse(fs.readFileSync(statusFile, 'utf8'));
                scenarioToUse = status.scenario;
            }
        }

        if (!scenarioToUse) {
            return res.status(400).json({ success: false, error: 'No scenario specified and no running scenario found' });
        }

        console.log(`Restarting simulation with scenario: ${scenarioToUse}`);

        // Stop current simulation using SimulationManager
        try {
            await simulationManager.stopSimulation();
        } catch (err) {
            console.log('SimulationManager stop failed, using direct PM2 delete:', err.message);
            await execPromise('pm2 delete all || true');
            // Force state update
            simulationManager.currentState = simulationManager.states.STOPPED;
            simulationManager.currentScenario = null;
            await simulationManager.updateStatusFile();
        }

        // Wait for cleanup
        await new Promise(resolve => setTimeout(resolve, 2000));

        // Load and start the scenario again using SimulationManager
        await simulationManager.loadScenario(scenarioToUse);
        await simulationManager.startSimulation();

        res.json({
            success: true,
            message: 'Simulation restarted successfully',
            scenario: simulationManager.currentScenario
        });
    } catch (error) {
        console.error('Error restarting simulation:', error);
        res.status(500).json({
            success: false,
            error: error.message,
            stderr: error.stderr
        });
    }
});

// Start recording (restarts orchestrator with DATA_DUMP=true)
app.post('/api/simulation/startRecording', async (req, res) => {
    try {
        const result = await simulationManager.startRecording();
        res.json({ success: true, ...result, recording: true });
    } catch (error) {
        console.error('Error starting recording:', error);
        res.status(500).json({ success: false, error: error.message });
    }
});

// Stop recording (restarts orchestrator with DATA_DUMP=false)
app.post('/api/simulation/stopRecording', async (req, res) => {
    try {
        const result = await simulationManager.stopRecording();
        res.json({ success: true, ...result, recording: false });
    } catch (error) {
        console.error('Error stopping recording:', error);
        res.status(500).json({ success: false, error: error.message });
    }
});

// Teleoperation process tracking
const teleoperationProcesses = new Map();

function isLoopbackHostname(hostname) {
    if (!hostname) return false;
    const normalized = hostname.toLowerCase().replace(/^\[/, '').replace(/\]$/, '');
    return normalized === 'localhost' ||
        normalized === '::1' ||
        normalized === '0:0:0:0:0:0:0:1' ||
        normalized === '127.0.0.1' ||
        /^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(normalized);
}

function isLoopbackAddress(address) {
    if (!address) return false;
    const normalized = address.replace(/^::ffff:/, '');
    return isLoopbackHostname(normalized);
}

function getOriginHostname(req) {
    const origin = req.get('origin') || req.get('referer');
    if (!origin) return null;

    try {
        return new URL(origin).hostname;
    } catch (_) {
        return null;
    }
}

function isLocalTeleoperationRequest(req) {
    const originHostname = getOriginHostname(req);
    if (originHostname && !isLoopbackHostname(originHostname)) {
        return false;
    }

    const forwardedFor = req.get('x-forwarded-for');
    if (forwardedFor) {
        const firstForwardedAddress = forwardedFor.split(',')[0].trim();
        if (!isLoopbackAddress(firstForwardedAddress)) {
            return false;
        }
    }

    return isLoopbackAddress(req.socket?.remoteAddress) || isLoopbackAddress(req.ip);
}

function findCarlaEggs(carlaHome) {
    const searchDirs = [
        carlaHome && path.join(carlaHome, 'PythonAPI', 'carla', 'dist'),
        carlaHome && path.join(carlaHome, 'carla', 'dist'),
        path.resolve(PROJECT_ROOT, '..', 'carla', 'dist'),
        path.resolve(PROJECT_ROOT, '..', 'carla', 'PythonAPI', 'carla', 'dist')
    ].filter(Boolean);

    const eggs = [];
    for (const searchDir of searchDirs) {
        if (!fs.existsSync(searchDir)) continue;
        for (const file of fs.readdirSync(searchDir)) {
            if (file.startsWith('carla-') && file.endsWith('.egg')) {
                eggs.push(path.join(searchDir, file));
            }
        }
    }
    return eggs;
}

function buildTeleoperationEnv() {
    const carlaConfig = globalConfig.carla || {};
    const carlaHome = process.env.CARLA_HOME || carlaConfig.python_api_path || carlaConfig.installation_path || '';
    const fallbackDisplay = fs.existsSync('/tmp/.X11-unix/X0') ? ':0' : '';
    const fallbackRuntimeDir = `/run/user/${process.getuid ? process.getuid() : 1000}`;
    const fallbackXauthority = path.join(os.homedir(), '.Xauthority');
    const pythonPaths = [
        path.join(PROJECT_ROOT, 'src'),
        carlaHome,
        ...findCarlaEggs(carlaHome),
        process.env.PYTHONPATH
    ].filter(Boolean);

    return {
        ...process.env,
        CARLA_HOME: carlaHome,
        DISPLAY: process.env.DISPLAY || fallbackDisplay,
        XAUTHORITY: process.env.XAUTHORITY || (fs.existsSync(fallbackXauthority) ? fallbackXauthority : ''),
        XDG_RUNTIME_DIR: process.env.XDG_RUNTIME_DIR || (fs.existsSync(fallbackRuntimeDir) ? fallbackRuntimeDir : ''),
        XDG_SESSION_TYPE: process.env.XDG_SESSION_TYPE || (fallbackDisplay ? 'x11' : ''),
        PYTHONPATH: pythonPaths.join(path.delimiter)
    };
}

function resolveTeleoperationPython() {
    const explicitPython = process.env.TELEOPERATION_PYTHON || process.env.CARLA_PYTHON;
    if (explicitPython) {
        if (path.isAbsolute(explicitPython) && !fs.existsSync(explicitPython)) {
            throw new Error(`Configured teleoperation Python does not exist: ${explicitPython}`);
        }
        return explicitPython;
    }

    const candidates = [
        path.join(os.homedir(), 'anaconda3', 'envs', 'carla', 'bin', 'python'),
        path.join(os.homedir(), 'miniconda3', 'envs', 'carla', 'bin', 'python'),
        'python3'
    ];

    return candidates.find(candidate => !path.isAbsolute(candidate) || fs.existsSync(candidate));
}

function canConnectToPort(host, port, timeoutMs = 1000) {
    return new Promise((resolve) => {
        const socket = net.createConnection({ host, port });
        let settled = false;

        const finish = (result) => {
            if (settled) return;
            settled = true;
            socket.destroy();
            resolve(result);
        };

        socket.setTimeout(timeoutMs);
        socket.on('connect', () => finish(true));
        socket.on('timeout', () => finish(false));
        socket.on('error', () => finish(false));
    });
}

// Start teleoperation GUI
app.post('/api/teleoperation/start', async (req, res) => {
    const { vehicleId, host = '127.0.0.1', port = 2000 } = req.body;
    const observeOnly = req.body.observeOnly !== undefined ? Boolean(req.body.observeOnly) : !vehicleId;
    
    try {
        if (!isLocalTeleoperationRequest(req)) {
            return res.status(403).json({
                success: false,
                error: 'Teleoperation can only be launched from the local machine. Open the UI at http://localhost:5173.'
            });
        }

        const carlaReachable = await canConnectToPort(host, Number(port));
        if (!carlaReachable) {
            return res.status(503).json({
                success: false,
                error: `CARLA is not reachable at ${host}:${port}. Start CARLA or load a scenario before launching teleoperation.`
            });
        }

        // Check if teleoperation is already running
        if (teleoperationProcesses.has('current')) {
            const existingProcess = teleoperationProcesses.get('current');
            // Check if process is still running
            try {
                process.kill(existingProcess.pid, 0); // Signal 0 just checks if process exists
                return res.status(400).json({ 
                    success: false, 
                    error: 'Teleoperation GUI is already running' 
                });
            } catch (err) {
                // Process doesn't exist, remove from map
                teleoperationProcesses.delete('current');
            }
        }

        // Get the path to the teleoperation script
        const scriptPath = path.join(PROJECT_ROOT, 'teleoperation', 'v2x_fleet_monitor.py');
        
        // Check if script exists
        if (!fs.existsSync(scriptPath)) {
            return res.status(404).json({ 
                success: false, 
                error: 'Teleoperation script not found' 
            });
        }

        // Build command arguments
        const args = [
            scriptPath,
            '--host', host,
            '--port', port.toString(),
            '--res', '1920x1080'
        ];
        
        // Add vehicle ID if provided
        if (vehicleId) {
            args.push('--vehicle-id', vehicleId.toString());
        }
        if (observeOnly) {
            args.push('--observe-only');
        }

        const pythonExecutable = resolveTeleoperationPython();
        const teleoperationEnv = buildTeleoperationEnv();
        let startupOutput = '';

        // Start the Python process using spawn for better process management
        const teleopProcess = spawn(pythonExecutable, args, {
            cwd: PROJECT_ROOT,
            env: teleoperationEnv,
            detached: false, // Keep attached so we can track it
            stdio: ['ignore', 'pipe', 'pipe'] // Ignore stdin, pipe stdout/stderr
        });

        // Store process reference
        teleoperationProcesses.set('current', teleopProcess);

        const appendStartupOutput = (data) => {
            startupOutput = `${startupOutput}${data.toString()}`;
            if (startupOutput.length > 4000) {
                startupOutput = startupOutput.slice(-4000);
            }
        };

        // Handle process exit
        teleopProcess.on('exit', (code, signal) => {
            console.log(`[Teleoperation] Process exited with code ${code}, signal ${signal}`);
            teleoperationProcesses.delete('current');
        });

        // Handle process error
        teleopProcess.on('error', (error) => {
            console.error('[Teleoperation] Process error:', error);
            teleoperationProcesses.delete('current');
        });

        // Log output for debugging
        teleopProcess.stdout?.on('data', (data) => {
            appendStartupOutput(data);
            console.log(`[Teleoperation] ${data.toString().trim()}`);
        });

        teleopProcess.stderr?.on('data', (data) => {
            appendStartupOutput(data);
            console.error(`[Teleoperation] ${data.toString().trim()}`);
        });

        await new Promise(resolve => setTimeout(resolve, 1500));
        if (teleopProcess.exitCode !== null || teleopProcess.killed) {
            teleoperationProcesses.delete('current');
            return res.status(500).json({
                success: false,
                error: `Teleoperation exited during startup${startupOutput ? `: ${startupOutput.trim()}` : ''}`,
                pid: teleopProcess.pid,
                python: pythonExecutable
            });
        }

        res.json({ 
            success: true, 
            message: 'Teleoperation GUI started',
            pid: teleopProcess.pid,
            python: pythonExecutable,
            display: teleoperationEnv.DISPLAY
        });
    } catch (error) {
        console.error('Error starting teleoperation:', error);
        res.status(500).json({ 
            success: false, 
            error: error.message 
        });
    }
});

// Stop teleoperation GUI
app.post('/api/teleoperation/stop', async (req, res) => {
    try {
        if (!teleoperationProcesses.has('current')) {
            return res.json({ 
                success: false, 
                error: 'No teleoperation process running' 
            });
        }

        const teleopProcess = teleoperationProcesses.get('current');
        
        // Try graceful shutdown first
        try {
            process.kill(teleopProcess.pid, 'SIGTERM');
            
            // Wait a bit for graceful shutdown
            await new Promise(resolve => setTimeout(resolve, 1000));
            
            // Check if still running, force kill if needed
            try {
                process.kill(teleopProcess.pid, 0);
                process.kill(teleopProcess.pid, 'SIGKILL');
            } catch (err) {
                // Process already terminated
            }
        } catch (err) {
            console.error('Error stopping teleoperation:', err);
        }

        teleoperationProcesses.delete('current');
        
        res.json({ 
            success: true, 
            message: 'Teleoperation GUI stopped' 
        });
    } catch (error) {
        console.error('Error stopping teleoperation:', error);
        res.status(500).json({ 
            success: false, 
            error: error.message 
        });
    }
});

// Get teleoperation status
app.get('/api/teleoperation/status', (req, res) => {
    const isRunning = teleoperationProcesses.has('current');
    let pid = null;
    let actuallyRunning = false;
    
    if (isRunning) {
        const teleopProcess = teleoperationProcesses.get('current');
        pid = teleopProcess.pid;
        
        // Check if process actually exists
        try {
            process.kill(pid, 0); // Signal 0 just checks if process exists
            actuallyRunning = true;
        } catch (err) {
            // Process doesn't exist, clean up
            teleoperationProcesses.delete('current');
            actuallyRunning = false;
        }
    }
    
    res.json({ 
        success: true, 
        running: actuallyRunning,
        pid: actuallyRunning ? pid : null
    });
});

app.get('/api/health', (req, res) => {
    res.json({ success: true, status: 'ok' });
});

// Error handlers to prevent server crashes
process.on('uncaughtException', (err) => {
    // Check if it's a PM2 null client error
    if (err.message && (err.message.includes('Cannot read properties of null') || 
        err.message.includes('reading \'call\''))) {
        console.error('[Server] PM2 connection error (caught):', err.message);
        // Don't crash - PM2 connection issues are handled gracefully
        return;
    }
    console.error('[Server] Uncaught Exception:', err);
    // For other errors, log but don't crash in production
    if (process.env.NODE_ENV !== 'production') {
        process.exit(1);
    }
});

process.on('unhandledRejection', (reason, promise) => {
    // Check if it's a PM2-related error
    if (reason && reason.message && 
        (reason.message.includes('Cannot read properties of null') || 
         reason.message.includes('reading \'call\'') ||
         reason.message.includes('PM2 connection'))) {
        console.error('[Server] PM2 connection error (unhandled rejection):', reason.message);
        return;
    }
    console.error('[Server] Unhandled Rejection at:', promise, 'reason:', reason);
});

// Start server
server.listen(PORT, HOST, () => {
    console.log(`🚀 Simulator Control Server running on http://${HOST}:${PORT}`);
    if (HOST === '0.0.0.0') {
        console.log(`   Local: http://localhost:${PORT}`);
    }
});

// Periodic status updates via WebSocket
setInterval(async () => {
    try {
        await connectPM2();
        const processes = await getProcessList();

        const services = processes.map(proc => ({
            name: proc.name,
            status: proc.pm2_env.status,
            cpu: proc.monit.cpu,
            memory: proc.monit.memory
        }));

        broadcast({ type: 'status_update', services });
        try {
            pm2.disconnect();
        } catch (disconnectErr) {
            // Ignore disconnect errors
        }
    } catch (error) {
        // Check if it's a PM2 connection error
        if (error.message && (error.message.includes('Cannot read properties of null') || 
            error.message.includes('reading \'call\''))) {
            // PM2 connection issue, will retry on next interval
            return;
        }
        console.error('Error broadcasting status:', error.message);
    }
}, 2000); // Update every 2 seconds
