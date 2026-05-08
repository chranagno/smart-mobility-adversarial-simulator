import { useState, useEffect, useRef } from 'react';
import { Play, Square, Circle, Loader2, Radio, Pause, FolderOpen, Activity, Clock } from 'lucide-react';

const SimulationControls = ({ api, currentScenario, services, simulationStatus, onRefresh, onLoadScenario }) => {
  const [simulationState, setSimulationState] = useState('idle'); // idle, stopped, loading, loaded, starting, running, stopping
  const [isRecording, setIsRecording] = useState(false);
  const [recordingDuration, setRecordingDuration] = useState(0);
  const [simulationTime, setSimulationTime] = useState(0);
  const [error, setError] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [liveStatus, setLiveStatus] = useState({
    connected: false,
    frame: null,
    simSeconds: null,
    phase: 'idle',
    recordingVehicles: 0,
    attacksEnabled: false,
    updatedAt: null,
  });
  const refreshIntervalRef = useRef(null);

  //
  // Live runtime status from the orchestrator WebSocket.
  //
  useEffect(() => {
    let ws;
    let reconnectTimer;

    const connect = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const isLocalDev = ['localhost', '127.0.0.1'].includes(window.location.hostname) &&
        (window.location.port === '5173' || window.location.port === '4173');
      const host = isLocalDev ? `${window.location.hostname}:3001` : window.location.host;
      ws = new WebSocket(`${protocol}//${host}/vehicles`);

      ws.onopen = () => {
        setLiveStatus(prev => ({ ...prev, connected: true, updatedAt: Date.now() }));
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type !== 'ego_sensor_data') return;

          setLiveStatus({
            connected: true,
            frame: data.frame ?? null,
            simSeconds: data.sim_seconds ?? null,
            phase: data.status?.phase || (data.recording ? 'recording' : 'streaming'),
            recordingVehicles: data.status?.recording_vehicles ?? data.recording_vehicles ?? 0,
            attacksEnabled: Boolean(data.status?.attacks_enabled),
            updatedAt: Date.now(),
          });
        } catch (_) {
          // Ignore non-status messages on the shared vehicle stream.
        }
      };

      ws.onclose = () => {
        setLiveStatus(prev => ({ ...prev, connected: false }));
        reconnectTimer = setTimeout(connect, 3000);
      };

      ws.onerror = () => {
        setLiveStatus(prev => ({ ...prev, connected: false }));
      };
    };

    connect();

    return () => {
      clearTimeout(reconnectTimer);
      if (ws) ws.close();
    };
  }, []);
  //
  // 🔄 Sync simulation state from simulationStatus prop if available
  //
  useEffect(() => {
    if (simulationStatus && simulationStatus.state) {
      setSimulationState(simulationStatus.state);
    }
  }, [simulationStatus]);

  //
  // 🔄 Derive simulation state from PM2 service list (orchestrator handles everything)
  //
  useEffect(() => {
    if (!services) return;

    // Don't override if we have simulationStatus prop
    if (simulationStatus && simulationStatus.state) {
      return;
    }

    const orchestrator = services.find(s => s.name === 'orchestrator');
    const sumo = services.find(s => s.name === 'sumo-server');
    const artery = services.find(s => s.name === 'artery');

    const sumoReady = sumo?.status === 'online';
    const arteryReady = artery ? artery.status === 'online' : true; // Artery is optional

    // Infrastructure is fully loaded when:
    // - sumo-server is online
    // - artery (if enabled) is online
    // Orchestrator is NOT required for "loaded" state - it starts when Play is pressed
    const infrastructureLoaded = sumoReady && arteryReady;

    // Check if any infrastructure service is still launching/starting
    const infrastructureLaunching = 
      (orchestrator?.status === 'launching') ||
      (sumo?.status === 'launching') ||
      (artery && artery.status === 'launching');

    // If orchestrator is stopping/launching but other infrastructure (sumo, artery) is still running,
    // this is likely a restart (e.g., for recording) - keep state as "running"
    // We check if infrastructure is still running rather than checking simulationState to avoid
    // timing issues when starting/stopping recording
    const isOrchestratorRestart = (orchestrator?.status === 'stopping' || orchestrator?.status === 'launching') &&
                                   (sumoReady || arteryReady);

    if (orchestrator?.status === 'online') {
      setSimulationState('running');
      setIsLoading(false); // Clear loading flag when running
    } else if (isOrchestratorRestart) {
      // During orchestrator restart (e.g., for recording), keep state as "running" 
      // to avoid showing "stopping" when simulation is still active
      // If infrastructure is still running, the simulation is still active, so keep it as "running"
      // unless we're in an idle/stopped state (which would indicate a full shutdown)
      if (simulationState !== 'idle' && simulationState !== 'stopped') {
        setSimulationState('running');
      } else {
        // If we were idle/stopped, treat orchestrator launch as "starting"
        setSimulationState('starting');
      }
    } else if (orchestrator?.status === 'launching') {
      setSimulationState('starting');
    } else if (orchestrator?.status === 'stopping') {
      setSimulationState('stopping');
    } else if (infrastructureLoaded) {
      // All infrastructure is ready: orchestrator, sumo and artery running
      setSimulationState('loaded');
      setIsLoading(false); // Clear loading flag when loaded
      // Clear any pending refresh interval
      if (refreshIntervalRef.current) {
        clearInterval(refreshIntervalRef.current);
        refreshIntervalRef.current = null;
      }
    } else if (infrastructureLaunching || orchestrator || sumo) {
      // Services are still starting/loading
      // Only set to loading if we actually have services starting
      // Don't override if we're already in a different state due to user action
      if (simulationState !== 'stopped' || orchestrator || sumo) {
        setSimulationState('loading');
      }
    } else {
      // Only set to idle if we truly have no services
      if (!orchestrator && !sumo && !artery) {
        setSimulationState('idle');
        setIsLoading(false);
      }
    }
  }, [services]);

  //
  // 🕒 Simulation runtime counter
  //
  useEffect(() => {
    if (simulationState !== 'running') {
      if (simulationState === 'stopped' || simulationState === 'idle') {
        setSimulationTime(0);
      }
      return;
    }
    const interval = setInterval(() => setSimulationTime(prev => prev + 1), 1000);
    return () => clearInterval(interval);
  }, [simulationState]);

  //
  // ⏺ Recording counter
  //
  useEffect(() => {
    if (!isRecording) {
      setRecordingDuration(0);
      return;
    }
    const interval = setInterval(() => setRecordingDuration(prev => prev + 1), 1000);
    return () => clearInterval(interval);
  }, [isRecording]);

  const formatTime = (seconds) => {
    const h = Math.floor(seconds / 3600).toString().padStart(2, '0');
    const m = Math.floor((seconds % 3600) / 60).toString().padStart(2, '0');
    const s = (seconds % 60).toString().padStart(2, '0');
    return `${h}:${m}:${s}`;
  };

  const getStatusMessage = () => {
    if (simulationState === 'idle') return 'Simulator shell is ready. Load a scenario to start infrastructure.';
    if (simulationState === 'loading') return 'Loading scenario infrastructure.';
    if (simulationState === 'loaded') return 'Infrastructure is loaded. Press Play to start the orchestrator.';
    if (simulationState === 'starting') return 'Starting orchestrator and synchronizing simulators.';
    if (simulationState === 'stopping') return 'Stopping simulation services.';
    if (simulationState !== 'running') return 'Simulation is stopped.';
    if (!liveStatus.connected) return 'Running, waiting for orchestrator status stream.';
    if (liveStatus.phase === 'recording') {
      return liveStatus.attacksEnabled
        ? 'Recording sensor frames and adversarial attack outputs.'
        : 'Recording sensor frames; model server unavailable, attacks paused.';
    }
    return 'Streaming live telemetry from the orchestrator.';
  };

  //
  // ▶ PLAY simulation (Start only after loaded)
  //
  const handlePlay = async () => {
    try {
      setError(null);
      setSimulationTime(0);

      if (simulationState !== 'loaded') {
        throw new Error('Load the scenario first, then press Play to start the sync service.');
      }

      // Now start the simulation
      setSimulationState('starting');
      const result = await api.simulation.start();

      if (!result.success) throw new Error(result.error);

      setIsLoading(false);
    } catch (err) {
      const previousState = simulationState === 'loading' ? 'idle' : 'loaded';
      setSimulationState(previousState);
      setError(err.message);
      setIsLoading(false);
    }
  };

  //
  // ⏹ STOP simulation and all services
  //
  const handleStop = async () => {
    try {
      setError(null);
      setSimulationState('stopping');

      // Stop simulation (now stops all services and returns to idle)
      const result = await api.simulation.stop();
      if (!result.success) {
        throw new Error(result.error || 'Failed to stop simulation');
      }

      // Refresh services list
      if (onRefresh) {
        setTimeout(() => {
          onRefresh();
        }, 1000);
      }

      // Set state to idle
      setSimulationState('idle');
      setSimulationTime(0);
      setIsRecording(false);
      setRecordingDuration(0);
      setIsLoading(false);
    } catch (err) {
      setSimulationState('idle');
      setError(err.message);
      setIsLoading(false);
    }
  };

  //
  // ⏺ RECORD toggle
  //
  const handleRecordToggle = async () => {
    try {
      setError(null);
      if (isRecording) {
        const result = await api.simulation.stopRecording();
        if (result.success) {
          setIsRecording(false);
          setRecordingDuration(0);
        } else {
          throw new Error(result.error || 'Failed to stop recording');
        }
      } else {
        const result = await api.simulation.startRecording({ format: "json" });
        if (result.success) {
          setIsRecording(true);
          setRecordingDuration(0);
        } else {
          throw new Error(result.error || 'Failed to start recording');
        }
      }
    } catch (err) {
      setError(err.message);
    }
  };

  //
  // Dynamic button states
  //
  const runningServicesCount = services?.filter(s => s.status === 'online').length || 0;
  const totalServices = services?.length || 0;

  // Play button is enabled when:
  // - Scenario is loaded (ready to start)
  const isPlayDisabled = simulationState !== 'loaded' || isLoading;
  const isPauseDisabled = simulationState !== 'running' || isLoading;
  const isStopDisabled = (simulationState === 'idle' || simulationState === 'stopped' || simulationState === 'loading') || isLoading;
  const isRecordDisabled = simulationState !== 'running';
  const isLoadScenarioDisabled = simulationState === 'loaded' || simulationState === 'loading' || simulationState === 'running' || simulationState === 'starting' || isLoading;

  // Handle pause functionality (for now, pause = stop)
  const handlePause = async () => {
    await handleStop();
  };

  return (
    <div className="bg-gradient-to-br from-slate-800 to-slate-900 rounded-xl shadow-2xl border border-slate-700 overflow-hidden">
      {/* Player Header */}
      <div className="bg-slate-900/50 px-6 py-4 border-b border-slate-700">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-12 h-12 bg-gradient-to-br from-blue-500 to-blue-600 rounded-lg flex items-center justify-center shadow-lg">
              <Radio className="w-6 h-6 text-white" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-white">Simulation Player</h2>
              <p className="text-sm text-slate-400">
                {currentScenario ? `Scenario: ${currentScenario}` : 'No scenario selected'}
              </p>
            </div>
          </div>

          {/* Status Indicator */}
          <div className="flex items-center gap-2 px-4 py-2 bg-slate-800 rounded-lg border border-slate-700">
            <div className={`
              w-3 h-3 rounded-full
              ${simulationState === 'running' ? 'bg-green-500 animate-pulse shadow-lg shadow-green-500/50' :
                simulationState === 'loaded' ? 'bg-blue-500' :
                  simulationState === 'starting' || simulationState === 'stopping' || simulationState === 'loading' ? 'bg-yellow-500 animate-pulse' :
                    'bg-slate-500'}
            `}></div>
            <span className="text-slate-300 capitalize text-sm font-medium">
              {simulationState === 'running' ? 'Running' :
                simulationState === 'starting' ? 'Starting...' :
                  simulationState === 'stopping' ? 'Stopping...' :
                    simulationState === 'loaded' ? 'Loaded' :
                      simulationState === 'loading' ? 'Loading...' :
                        simulationState === 'idle' ? 'Idle' :
                          'Stopped'}
            </span>
          </div>
        </div>
      </div>

      {/* Error Display */}
      {error && (
        <div className="mx-6 mt-4 bg-red-900/30 border border-red-700/50 text-red-200 px-4 py-3 rounded-lg">
          <p className="text-sm font-medium">{error}</p>
        </div>
      )}

      {/* Main Player Controls */}
      <div className="px-6 py-8">
        {/* Primary Control Buttons */}
        <div className="flex items-center justify-center gap-6 mb-8">
          {/* Load Scenario Button */}
          {onLoadScenario && (
            <button
              onClick={onLoadScenario}
              disabled={isLoadScenarioDisabled}
              className={`
                relative group
                w-20 h-20 rounded-full flex items-center justify-center
                transition-all duration-200 transform
                ${isLoadScenarioDisabled
                  ? 'bg-slate-700 opacity-40 cursor-not-allowed'
                  : 'bg-blue-600 hover:bg-blue-700 hover:scale-110 active:scale-95 shadow-lg shadow-blue-600/30'}
              `}
              title={
                simulationState === 'loaded' ? 'Scenario already loaded' :
                  simulationState === 'loading' ? 'Loading scenario...' :
                    simulationState === 'running' ? 'Stop simulation to load new scenario' :
                      'Load Scenario'
              }
            >
              {simulationState === 'loading' ? (
                <Loader2 className="w-10 h-10 text-white animate-spin" />
              ) : (
                <FolderOpen className="w-10 h-10 text-white" />
              )}
              {!isLoadScenarioDisabled && (
                <div className="absolute inset-0 rounded-full bg-blue-400 opacity-0 group-hover:opacity-20 animate-ping"></div>
              )}
            </button>
          )}

          {/* Play/Pause Button */}
          {simulationState === 'running' ? (
            <button
              onClick={handlePause}
              disabled={isPauseDisabled}
              className={`
                relative group
                w-20 h-20 rounded-full flex items-center justify-center
                transition-all duration-200 transform
                ${isPauseDisabled
                  ? 'bg-slate-700 opacity-40 cursor-not-allowed'
                  : 'bg-yellow-600 hover:bg-yellow-700 hover:scale-110 active:scale-95 shadow-lg shadow-yellow-600/30'}
              `}
              title="Pause simulation"
            >
              <Pause className="w-10 h-10 text-white" fill="currentColor" />
              {!isPauseDisabled && (
                <div className="absolute inset-0 rounded-full bg-yellow-400 opacity-0 group-hover:opacity-20 animate-ping"></div>
              )}
            </button>
          ) : (
            <button
              onClick={handlePlay}
              disabled={isPlayDisabled}
              className={`
                relative group
                w-20 h-20 rounded-full flex items-center justify-center
                transition-all duration-200 transform
                ${isPlayDisabled
                  ? 'bg-slate-700 opacity-40 cursor-not-allowed'
                  : 'bg-green-600 hover:bg-green-700 hover:scale-110 active:scale-95 shadow-lg shadow-green-600/30'}
              `}
              title={simulationState !== 'loaded' ? 'Load scenario first, then press Play' : 'Start simulation'}
            >
              {simulationState === 'starting' ? (
                <Loader2 className="w-10 h-10 text-white animate-spin" />
              ) : (
                <Play className="w-10 h-10 text-white ml-1" fill="currentColor" />
              )}
              {!isPlayDisabled && (
                <div className="absolute inset-0 rounded-full bg-green-400 opacity-0 group-hover:opacity-20 animate-ping"></div>
              )}
            </button>
          )}

          {/* Stop Button */}
          <button
            onClick={handleStop}
            disabled={isStopDisabled}
            className={`
              relative group
              w-20 h-20 rounded-full flex items-center justify-center
              transition-all duration-200 transform
              ${isStopDisabled
                ? 'bg-slate-700 opacity-40 cursor-not-allowed'
                : 'bg-red-600 hover:bg-red-700 hover:scale-110 active:scale-95 shadow-lg shadow-red-600/30'}
            `}
            title={simulationState === 'running' ? 'Stop simulation' : 'Stop simulation'}
          >
            {simulationState === 'stopping' ? (
              <Loader2 className="w-10 h-10 text-white animate-spin" />
            ) : (
              <Square className="w-8 h-8 text-white" fill="currentColor" />
            )}
            {!isStopDisabled && (
              <div className="absolute inset-0 rounded-full bg-red-400 opacity-0 group-hover:opacity-20 animate-ping"></div>
            )}
          </button>

          {/* Record Button */}
          <button
            onClick={handleRecordToggle}
            disabled={isRecordDisabled}
            className={`
              relative group
              w-20 h-20 rounded-full flex items-center justify-center
              transition-all duration-200 transform
              ${isRecording
                ? 'bg-red-600 animate-pulse shadow-lg shadow-red-600/50'
                : isRecordDisabled
                  ? 'bg-slate-700 opacity-40 cursor-not-allowed'
                  : 'bg-slate-600 hover:bg-slate-700 hover:scale-110 active:scale-95 shadow-lg'}
            `}
            title={isRecordDisabled ? 'Start simulation to record' : isRecording ? 'Stop recording' : 'Start recording'}
          >
            <Circle
              className={`w-10 h-10 text-white ${isRecording ? 'fill-current' : ''}`}
              strokeWidth={isRecording ? 0 : 2}
            />
            {isRecording && (
              <div className="absolute inset-0 rounded-full bg-red-400 opacity-30 animate-ping"></div>
            )}
            {!isRecordDisabled && !isRecording && (
              <div className="absolute inset-0 rounded-full bg-slate-400 opacity-0 group-hover:opacity-20 animate-ping"></div>
            )}
          </button>
        </div>

        {/* Time Display and Stats */}
        <div className="bg-slate-900/50 rounded-lg p-6 border border-slate-700">
          <div className="flex items-center justify-between mb-4">
            {/* Simulation Time */}
            <div className="flex flex-col">
              <span className="text-xs text-slate-400 mb-1">Simulation Time</span>
              <span className="text-3xl font-mono font-bold text-white">
                {formatTime(simulationTime)}
              </span>
            </div>

            {/* Recording Time */}
            {isRecording && (
              <div className="flex flex-col items-end">
                <span className="text-xs text-red-400 mb-1 flex items-center gap-2">
                  <div className="w-2 h-2 bg-red-500 rounded-full animate-pulse"></div>
                  Recording
                </span>
                <span className="text-3xl font-mono font-bold text-red-400">
                  {formatTime(recordingDuration)}
                </span>
              </div>
            )}
          </div>

          {/* Runtime Status Box */}
          <div className="mb-4 rounded-lg border border-slate-700 bg-slate-950/50 p-4">
            <div className="flex items-center justify-between gap-4 mb-3">
              <div className="flex items-center gap-2">
                <Activity className="w-4 h-4 text-cyan-400" />
                <span className="text-sm font-semibold text-white">Live Status</span>
              </div>
              <div className="flex items-center gap-2 text-xs text-slate-400">
                <div className={`w-2 h-2 rounded-full ${liveStatus.connected ? 'bg-green-500' : 'bg-slate-500'}`}></div>
                {liveStatus.connected ? 'Connected' : 'Waiting for orchestrator'}
              </div>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
              <div>
                <div className="text-xs text-slate-500">Frame</div>
                <div className="font-mono text-lg text-white">{liveStatus.frame ?? '—'}</div>
              </div>
              <div>
                <div className="text-xs text-slate-500">Seconds</div>
                <div className="font-mono text-lg text-white">
                  {liveStatus.simSeconds !== null ? Number(liveStatus.simSeconds).toFixed(1) : '—'}
                </div>
              </div>
              <div>
                <div className="text-xs text-slate-500">Vehicles</div>
                <div className="font-mono text-lg text-white">{liveStatus.recordingVehicles || 0}</div>
              </div>
              <div>
                <div className="text-xs text-slate-500">Attacks</div>
                <div className={`font-medium ${liveStatus.attacksEnabled ? 'text-green-400' : 'text-yellow-400'}`}>
                  {liveStatus.attacksEnabled ? 'Active' : 'Paused'}
                </div>
              </div>
            </div>

            <div className="flex items-start gap-2 text-sm text-slate-300">
              <Clock className="w-4 h-4 text-slate-500 mt-0.5" />
              <span>{getStatusMessage()}</span>
            </div>
          </div>

          {/* Service Status */}
          <div className="flex items-center justify-between pt-4 border-t border-slate-700">
            <div className="flex items-center gap-4">
              <span className="text-sm text-slate-400">
                Services: <span className="font-semibold text-white">{runningServicesCount}/{totalServices}</span>
              </span>
            </div>
            {simulationState === 'running' && (
              <div className="flex items-center gap-2 text-sm">
                <div className="w-2 h-2 bg-green-500 rounded-full animate-pulse"></div>
                <span className="text-green-400 font-medium">Active</span>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default SimulationControls;
