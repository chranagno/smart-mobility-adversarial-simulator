const fs = require('fs');
const yaml = require('js-yaml');
const path = require('path');

const PROJECT_ROOT = path.resolve(__dirname, '..');
const CONFIG_DIR = __dirname;

// Load global configuration (installation paths)
const globalConfigPath = path.join(CONFIG_DIR, 'global.config.yaml');
if (!fs.existsSync(globalConfigPath)) {
    console.error('Missing global.config.yaml file. Please create it with simulator installation paths.');
    process.exit(1);
}
const globalConfig = yaml.load(fs.readFileSync(globalConfigPath, 'utf8'));

// Load scenario configuration (runtime parameters) if explicitly provided.
const providedConfig = process.env.SIM_CONFIG;
// If a path is provided and not absolute, resolve it relative to repo root
const configPath = providedConfig
    ? (path.isAbsolute(providedConfig) ? providedConfig : path.join(PROJECT_ROOT, providedConfig))
    : null;
console.log('Scenario config:', configPath || 'not provided');
if (configPath && !fs.existsSync(configPath)) {
    console.error('Missing or invalid scenario config YAML file.');
    process.exit(1);
}

// Load scenario-specific config when available
const scenarioConfig = configPath
    ? (yaml.load(fs.readFileSync(configPath, 'utf8')) || {})
    : {};

// Try to load and merge general_config.yaml (if it exists in the same directory)
// This ensures Artery and other common settings are available
let generalConfig = {};
if (configPath) {
    const configDir = path.dirname(configPath);
    const generalConfigPath = path.join(configDir, 'general_config.yaml');
    if (fs.existsSync(generalConfigPath)) {
        try {
            generalConfig = yaml.load(fs.readFileSync(generalConfigPath, 'utf8')) || {};
            console.log('Loaded general_config.yaml and merging with scenario config');

            // Merge general config into scenario config (scenario config takes precedence)
            // Simple merge: scenario values override general values
            function mergeConfigs(general, scenario) {
                const merged = { ...general };
                for (const key in scenario) {
                    if (scenario[key] !== null && typeof scenario[key] === 'object' && !Array.isArray(scenario[key])) {
                        merged[key] = mergeConfigs(general[key] || {}, scenario[key]);
                    } else {
                        merged[key] = scenario[key];
                    }
                }
                return merged;
            }

            Object.assign(scenarioConfig, mergeConfigs(generalConfig, scenarioConfig));
        } catch (err) {
            console.warn(`Warning: Could not load general_config.yaml: ${err.message}`);
        }
    }
}

const DATA_DUMP_DIR = path.join(PROJECT_ROOT, 'data');

console.log('PROJECT_ROOT:', PROJECT_ROOT);
console.log('DATA_DUMP_DIR:', DATA_DUMP_DIR);

// Extract global paths
const carlaPaths = globalConfig.carla || {};
const sumoPaths = globalConfig.sumo || {};
const arteryPaths = globalConfig.artery || {};

// Extract scenario runtime config
const mode = scenarioConfig.mode || 'sumo_cosim';  // Default to SUMO co-simulation
const carla = scenarioConfig.carla || {};
let sumo = scenarioConfig.sumo || {};
let artery = scenarioConfig.artery || {};
const scenarioRunner = scenarioConfig.scenario_runner || {};
const town = carla.town || scenarioConfig.world?.town || 'Town01';
const carla_host = carla.host || '127.0.0.1';

// Handle Artery configuration: set default ini_path if enabled but not specified
// Also auto-enable Artery if ini_path is provided
if (artery.ini_path && artery.enabled === undefined) {
    artery.enabled = true;
    console.log(`Artery auto-enabled because ini_path is set: ${artery.ini_path}`);
}
if (artery.enabled && !artery.ini_path) {
    // Default to integrated-simulator omnetpp.ini in Artery installation
    const defaultIniPath = path.join(arteryPaths.installation_path, 'scenarios', 'integrated-simulator', 'omnetpp.ini');
    if (fs.existsSync(defaultIniPath)) {
        artery.ini_path = defaultIniPath;
        artery.enabled = true;
        console.log(`Using default Artery ini_path: ${artery.ini_path}`);
    } else {
        console.warn(`Warning: Artery is enabled but ini_path not specified and default not found: ${defaultIniPath}`);
        artery.enabled = false; // Disable if we can't find the ini file
    }
}

console.log(`Artery configuration - enabled: ${artery.enabled}, ini_path: ${artery.ini_path || 'not specified'}`);

// Handle OpenCDA YAML format: sumo_cfg_file should map to sumo.config
// The orchestrator uses sumo.config, but OpenCDA YAML might have sumo_cfg_file
if (sumo.sumo_cfg_file && !sumo.config) {
    sumo.config = sumo.sumo_cfg_file;
}

// Ensure SUMO is enabled if config is provided
if (sumo.config && sumo.enabled === undefined) {
    sumo.enabled = true;
}

console.log(`SUMO config: ${sumo.config || 'not specified'}, enabled: ${sumo.enabled}`);

console.log(`Simulation mode: ${mode}`);

// Helper function to build sync-simulators arguments
function buildSyncArgs(options = {}) {
    const sync = scenarioConfig.sync || {};
    const syncArgs = [
        `--sumo_cfg_file ${sumo.config}`,
        `--carla-host ${carla_host}`,
        `--carla-port ${carla.port || 2000}`,
        `--num-clients ${sumo.num_clients || 2}`,
        `--client-order 2`,
        `--step-length ${sync.step_length || 0.05}`,
        `--tls-manager ${sync.tls_manager || 'carla'}`,
    ];

    // Pass scenario config so sync script can spawn configured agents/sensors
    if (configPath) {
        syncArgs.push(`--scenario-config ${configPath}`);
    }

    // Optional flags
    if (sumo.gui) syncArgs.push('--sumo-gui');
    if (sync.vehicle_lights) syncArgs.push('--sync-vehicle-lights');
    if (sync.vehicle_color) syncArgs.push('--sync-vehicle-color');
    if (sync.vehicle_all) syncArgs.push('--sync-vehicle-all');
    if (sync.enable_ws) syncArgs.push('--enable-ws');
    if (scenarioConfig.debug) syncArgs.push('--debug');
    if (options.carlaMaster) syncArgs.push('--carla-master');

    // Artery integration (if enabled)
    // if (artery.enabled && artery.ini_path && artery.ini_path !== '/path/to/your/artery/omnetpp.ini') {
    //     syncArgs.push('--start-artery');
    //     if (arteryPaths.build_path) {
    //         syncArgs.push(`--artery-build-path ${arteryPaths.build_path}`);
    //     }
    // }

    return syncArgs;
}

// Define apps based on mode
const apps = [];

// 1. Scenario-dependent simulator services
if (configPath) {
    // Carla headless server
    apps.push({
        name: "carla-headless",
        script: path.join(PROJECT_ROOT, "scripts", "start_carla_headless.sh"),
        args: `--path ${carlaPaths.installation_path} --port ${carla.port || 2000} --quality ${carla.quality || 'Low'}`,
        autorestart: false,
        instances: 1,
        exec_mode: "fork",
        watch: false,
        env: {
            CARLA_PATH: carlaPaths.installation_path
        }
    });
}

// 2. Mode-specific services
if (configPath && mode === 'carla_scenario') {
    // Carla Scenario Runner mode
    console.log('Mode: Carla Scenario Runner with synchronization');

    // Scenario Runner (loads scenario into CARLA)
    // apps.push({
    //     name: "scenario-runner",
    //     script: "python3",
    //     args: `${carlaPaths.scenario_runner_executable} --scenario ${scenarioRunner.scenario_file} --host ${carla_host} --port ${carla.port || 2000} --timeout ${scenarioRunner.timeout || 300} --output ${scenarioRunner.output_dir || './scenario_results'}`,
    //     autorestart: false,
    //     watch: false,
    //     exec_mode: "fork",
    //     env: {
    //         CARLA_HOME: carlaPaths.python_api_path,
    //         SCENARIO_RUNNER_ROOT: carlaPaths.scenario_runner_path
    //     }
    // });

    // SUMO server (separate service, must start before sync)
    // NOTE: Uses wrapper script for proper startup and port checking
    // Start SUMO server if config is provided
    if (sumo.config) {
        console.log('Starting SUMO server');

        apps.push({
            name: "sumo-server",
            script: path.join(PROJECT_ROOT, "scripts", "start_sumo_service.sh"),
            autorestart: true,
            watch: false,
            exec_mode: "fork",
            instances: 1,
            env: {
                SUMO_HOME: sumoPaths.installation_path,
                SUMO_CFG: sumo.config,
                SUMO_PORT: sumo.port || 8813,
                NUM_CLIENTS: sumo.num_clients || 2,
                SUMO_GUI: sumo.gui || false,
                MAX_WAIT: 30
            }
        });

        // Artery service (if enabled, run as separate service)
        // NOTE: Artery must start AFTER SUMO port 8813 is online
        // The start_artery_service.sh script has built-in port checking
        if (artery.enabled && artery.ini_path) {
            console.log('Adding Artery as separate service (starts after SUMO port check)');
            console.log(`   Artery ini_path: ${artery.ini_path}`);
            apps.push({
                name: "artery",
                script: path.join(PROJECT_ROOT, "scripts", "start_artery_service.sh"),
                autorestart: false,
                watch: false,
                exec_mode: "fork",
                instances: 1,
                kill_timeout: 5000,
                wait_ready: false,  // Don't use PM2 wait_ready, script handles port checking
                env: {
                    ARTERY_DIR: arteryPaths.installation_path,
                    INI_FILE: artery.ini_path,
                    SUMO_PORT: sumo.port || 8813,
                    OMNETPP_ROOT: arteryPaths.omnetpp_root
                }
            });
        } else if (artery.enabled && !artery.ini_path) {
            console.warn('Warning: Artery is enabled but ini_path is not specified. Artery service will not start.');
        }

        // OpenCDA Orchestrator (replaces sync-simulators and config-launcher)
        // NOTE: Orchestrator extracts parameters from YAML and handles everything
        console.log('Adding  orchestrator');

        // Use the scenario YAML path directly - orchestrator will handle it
        // The configPath should be the OpenCDA YAML file selected from the GUI
        const scenarioYamlPath = configPath;

        apps.push({
            name: "orchestrator",
            script: path.join(PROJECT_ROOT, "scripts", "start_orchestrator.sh"),
            args: scenarioYamlPath,
            autorestart: true,
            watch: false,
            exec_mode: "fork",
            env: {
                SCENARIO_YAML: scenarioYamlPath,
                CARLA_HOME: carlaPaths.python_api_path,
                SUMO_HOME: sumoPaths.installation_path,
                OMNETPP_ROOT: arteryPaths.omnetpp_root,
                ARTERY_HOME: arteryPaths.installation_path,
                WS_PORT: "8765",
                START_ARTERY: artery.enabled ? "true" : "false",
                DATA_DUMP_DIR,
                DATA_DUMP: process.env.DATA_DUMP || "false",
                PYTHONPATH: `${path.join(__dirname, '..', 'src')}:${carlaPaths.python_api_path}:${carlaPaths.python_api_path}/carla/dist/carla-0.9.15-py3.7-linux-x86_64.egg${process.env.PYTHONPATH ? ':' + process.env.PYTHONPATH : ''}`
            }
        });
    }

} else if (mode === 'carla2sumo') {
    // Spawn everything in Carla via ScenarioRunner, mirror to SUMO/Artery
    console.log('Mode: Carla-driven scenario mirrored into SUMO/Artery');

    const runnerExecutable = carlaPaths.scenario_runner_executable;
    const scenarioFilePath = scenarioRunner.scenario_file
        ? (path.isAbsolute(scenarioRunner.scenario_file)
            ? scenarioRunner.scenario_file
            : path.join(process.cwd(), scenarioRunner.scenario_file))
        : '';

    if (runnerExecutable && scenarioFilePath) {
        const runnerArgs = [
            `--openscenario ${scenarioFilePath}`,
            `--host ${carla_host}`,
            `--port ${carla.port || 2000}`,
            `--timeout ${scenarioRunner.timeout || 300}`,
            `--outputDir ${scenarioRunner.output_dir || './scenario_results'}`,
            `--trafficManagerPort ${scenarioRunner.traffic_manager_port || 8001}`,
            '--sync'
        ];

        apps.push({
            name: "scenario-runner",
            script: "python3",
            args: `${runnerExecutable} ${runnerArgs.join(' ')}`,
            autorestart: false,
            watch: false,
            exec_mode: "fork",
            cwd: path.dirname(runnerExecutable),
            env: {
                CARLA_HOME: carlaPaths.python_api_path,
                SCENARIO_RUNNER_ROOT: carlaPaths.scenario_runner_path,
                PYTHONPATH: `${carlaPaths.python_api_path}:${carlaPaths.python_api_path}/carla/dist/carla-0.9.15-py3.7-linux-x86_64.egg:${carlaPaths.scenario_runner_path}${process.env.PYTHONPATH ? ':' + process.env.PYTHONPATH : ''}`
            }
        });
    } else {
        console.warn('Scenario Runner executable or scenario file missing for carla2sumo mode');
    }

    // Start SUMO server if config is provided
    if (sumo.config) {
        console.log('Starting SUMO server');

        apps.push({
            name: "sumo-server",
            script: path.join(PROJECT_ROOT, "scripts", "start_sumo_service.sh"),
            autorestart: true,
            watch: false,
            exec_mode: "fork",
            instances: 1,
            env: {
                SUMO_HOME: sumoPaths.installation_path,
                SUMO_CFG: sumo.config,
                SUMO_PORT: sumo.port || 8813,
                NUM_CLIENTS: sumo.num_clients || 2,
                SUMO_GUI: sumo.gui || false,
                MAX_WAIT: 30
            }
        });

        // Start Artery service if enabled and ini_path is set
        if (artery.enabled && artery.ini_path) {
            console.log('Adding Artery as separate service (starts after SUMO port check)');
            apps.push({
                name: "artery",
                script: path.join(PROJECT_ROOT, "scripts", "run_artery.sh"),
                autorestart: false,
                watch: false,
                exec_mode: "fork",
                instances: 1,
                kill_timeout: 5000,
                wait_ready: false,
                env: {
                    ARTERY_DIR: arteryPaths.installation_path,
                    INI_FILE: artery.ini_path,
                    SUMO_PORT: sumo.port || 8813,
                    OMNETPP_ROOT: arteryPaths.omnetpp_root
                }
            });
        }

        console.log('Adding synchronization bridge (CARLA master)');
        const syncArgs = buildSyncArgs({ carlaMaster: true }).filter(arg =>
            !arg.includes('start-artery') && !arg.includes('artery-build-path')
        );

        apps.push({
            name: "sync-simulators",
            script: path.join(PROJECT_ROOT, "scripts", "start_sync_simulators.sh"),
            args: syncArgs.join(' '),
            autorestart: true,
            watch: false,
            exec_mode: "fork",
            env: {
                CARLA_HOME: carlaPaths.python_api_path,
                SUMO_HOME: sumoPaths.installation_path,
                OMNETPP_ROOT: arteryPaths.omnetpp_root,
                ARTERY_HOME: arteryPaths.installation_path,
                CARLA_HOST: carla_host,
                CARLA_PORT: carla.port || 2000,
                MAX_WAIT: 120,
                SKIP_CONFIG_WAIT: "true",
                PYTHONPATH: `${carlaPaths.python_api_path}:${carlaPaths.python_api_path}/carla/dist/carla-0.9.15-py3.7-linux-x86_64.egg${process.env.PYTHONPATH ? ':' + process.env.PYTHONPATH : ''}`
            }
        });
    }

} else if (configPath && mode === 'sumo_cosim') {
    // SUMO co-simulation mode
    console.log('Mode: SUMO Co-simulation with synchronization');

    // Config launcher service (exposes API to configure Carla)
    // This is a persistent service that runs continuously
    apps.push({
        name: "config-launcher",
        script: path.join(PROJECT_ROOT, "scripts", "start_config_launcher_service.sh"),
        autorestart: true,
        watch: false,
        exec_mode: "fork",
        env: {
            CARLA_HOME: carlaPaths.python_api_path,
            CARLA_HOST: carla_host,
            CARLA_PORT: carla.port || 2000,
            CONFIG_LAUNCHER_HOST: "127.0.0.1",
            CONFIG_LAUNCHER_PORT: "5001",
            TOWN: town,  // Default town, can be overridden via API
            MAX_WAIT: 120,
            PYTHON_SCRIPTS_DIR: path.join(PROJECT_ROOT, "src"),
            PYTHONPATH: `${carlaPaths.python_api_path}:${carlaPaths.python_api_path}/carla/dist/carla-0.9.15-py3.7-linux-x86_64.egg${process.env.PYTHONPATH ? ':' + process.env.PYTHONPATH : ''}`
        }
    });

    // SUMO server (separate service, must start before sync)
    // NOTE: Uses wrapper script for proper startup and port checking
    console.log('Starting SUMO server');

    apps.push({
        name: "sumo-server",
        script: path.join(PROJECT_ROOT, "scripts", "start_sumo_service.sh"),
        autorestart: false,
        watch: false,
        exec_mode: "fork",
        instances: 1,
        env: {
            SUMO_HOME: sumoPaths.installation_path,
            SUMO_CFG: sumo.config,
            SUMO_PORT: sumo.port || 8813,
            NUM_CLIENTS: sumo.num_clients || 2,
            SUMO_GUI: sumo.gui || false,
            MAX_WAIT: 30
        }
    });

    // Artery service (if enabled, run as separate service)
    // NOTE: Artery must start AFTER SUMO port 8813 is online and stable
    // The run_artery.sh script waits for SUMO port + 5s stabilization delay
    if (artery.enabled && artery.ini_path) {
        console.log('Adding Artery as separate service (starts after SUMO port check)');
        apps.push({
            name: "artery",
            script: path.join(PROJECT_ROOT, "scripts", "run_artery.sh"),
            autorestart: false,
            watch: false,
            exec_mode: "fork",
            instances: 1,
            kill_timeout: 5000,
            wait_ready: false,  // Don't use PM2 wait_ready, script handles port checking
            env: {
                ARTERY_DIR: arteryPaths.installation_path,
                INI_FILE: artery.ini_path,
                SUMO_PORT: sumo.port || 8813,
                OMNETPP_ROOT: arteryPaths.omnetpp_root
            }
        });
    }

    // apps.push({
    //     name: "sync-simulators",
    //     script: path.join(PROJECT_ROOT, "scripts", "start_sync_simulators.sh"),
    //     args: syncArgs.join(' '),
    //     autorestart: true,
    //     watch: false,
    //     exec_mode: "fork",
    //     env: {
    //         CARLA_HOME: carlaPaths.python_api_path,
    //         SUMO_HOME: sumoPaths.installation_path,
    //         OMNETPP_ROOT: arteryPaths.omnetpp_root,
    //         ARTERY_HOME: arteryPaths.installation_path,
    //         CARLA_HOST: carla_host,
    //         CARLA_PORT: carla.port || 2000,
    //         MAX_WAIT: 300,  // 5 minutes max wait for config-launcher
    //         PYTHONPATH: `${path.join(__dirname, '..', 'src')}:${carlaPaths.python_api_path}:${carlaPaths.python_api_path}/carla/dist/carla-0.9.15-py3.7-linux-x86_64.egg${process.env.PYTHONPATH ? ':' + process.env.PYTHONPATH : ''}`
    //     }
    // });

    const scenarioYamlPath = configPath;

    apps.push({
        name: "orchestrator",
        script: path.join(PROJECT_ROOT, "scripts", "start_orchestrator.sh"),
        args: scenarioYamlPath,
        autorestart: true,
        watch: false,
        exec_mode: "fork",
        env: {
            SCENARIO_YAML: scenarioYamlPath,
            CARLA_HOME: carlaPaths.python_api_path,
            SUMO_HOME: sumoPaths.installation_path,
            OMNETPP_ROOT: arteryPaths.omnetpp_root,
            ARTERY_HOME: arteryPaths.installation_path,
            WS_PORT: "8765",
            START_ARTERY: artery.enabled ? "true" : "false",
            DATA_DUMP_DIR,
            DATA_DUMP: process.env.DATA_DUMP || "false",
            PYTHONPATH: `${path.join(__dirname, '..', 'src')}:${carlaPaths.python_api_path}:${carlaPaths.python_api_path}/carla/dist/carla-0.9.15-py3.7-linux-x86_64.egg${process.env.PYTHONPATH ? ':' + process.env.PYTHONPATH : ''}`
        }
    });

    // 3. Image capture service
    apps.push({
        name: "image-capture",
        script: "python3",
        args: `${path.join(PROJECT_ROOT, "src", "image_capture_service.py")} --carla-host ${carla_host} --carla-port ${carla.port || 2000} --auto-connect`,
        autorestart: true,
        watch: false,
        exec_mode: "fork",
        env: {
            CARLA_HOME: carlaPaths.python_api_path
        }
    });
}

// 4. Frontend backend API (Node/Express)
apps.push({
    name: "frontend-backend",
    script: "node",
    args: "index.js",
    cwd: path.join(PROJECT_ROOT, "frontend", "server"),
    autorestart: true,
    watch: false,
    env: {
        HOST: process.env.BACKEND_HOST || process.env.HOST || "0.0.0.0",
        PORT: process.env.PORT || 3001,
        VEHICLE_WS_PORT: process.env.VEHICLE_WS_PORT || 8765
    }
});

// 5. Frontend client (Vite dev server)
apps.push({
    name: "frontend-client",
    script: "npm",
    args: "run dev -- --host 0.0.0.0 --port 5173",
    cwd: path.join(PROJECT_ROOT, "frontend", "client"),
    autorestart: true,
    watch: false,
    env: {
        PORT: process.env.VITE_PORT || 5173,
    }
});

module.exports = {
    apps: apps
};
