import { useState, useEffect } from 'react';
import { Play, Square, RotateCw, Circle, Loader2 } from 'lucide-react';

const SimulationControls = ({ api, currentScenario, services }) => {
  const [simulationState, setSimulationState] = useState('stopped'); // stopped, starting, running, stopping
  const [isRecording, setIsRecording] = useState(false);
  const [recordingDuration, setRecordingDuration] = useState(0);
  const [simulationTime, setSimulationTime] = useState(0);
  const [error, setError] = useState(null);
  const [fps, setFps] = useState(0);

  // Derive simulation state from services
  useEffect(() => {
    if (!services) return;

    const syncSim = services.find(s => s.name === 'sync-simulators');

    if (syncSim?.status === 'online') {
      setSimulationState('running');
    } else if (syncSim?.status === 'launching') {
      setSimulationState('starting');
    } else if (syncSim?.status === 'stopping') {
      setSimulationState('stopping');
    } else {
      setSimulationState('stopped');
    }
  }, [services]);

  // Update simulation timer
  useEffect(() => {
    if (simulationState !== 'running') return;

    const interval = setInterval(() => {
      setSimulationTime(prev => prev + 1);
    }, 1000);

    return () => clearInterval(interval);
  }, [simulationState]);

  // Update recording timer
  useEffect(() => {
    if (!isRecording) return;

    const interval = setInterval(() => {
      setRecordingDuration(prev => prev + 1);
    }, 1000);

    return () => clearInterval(interval);
  }, [isRecording]);

  const formatTime = (seconds) => {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  };

  const handleStart = async () => {
    try {
      setError(null);
      setSimulationTime(0);
      setSimulationState('starting');

      const result = await api.simulation.start(currentScenario);

      if (!result.success) {
        throw new Error(result.error || 'Failed to start simulation');
      }
    } catch (err) {
      console.error('Failed to start simulation:', err);
      setError(err.message);
      setSimulationState('stopped');
    }
  };

  const handleStop = async () => {
    try {
      setError(null);
      setSimulationState('stopping');

      const result = await api.simulation.stop();

      if (!result.success) {
        throw new Error(result.error || 'Failed to stop simulation');
      }

      setSimulationTime(0);
    } catch (err) {
      console.error('Failed to stop simulation:', err);
      setError(err.message);
      setSimulationState('running');
    }
  };

  const handleRestart = async () => {
    try {
      setError(null);
      setSimulationState('stopping');

      const result = await api.simulation.restart(currentScenario);

      if (!result.success) {
        throw new Error(result.error || 'Failed to restart simulation');
      }

      setSimulationTime(0);
    } catch (err) {
      console.error('Failed to restart simulation:', err);
      setError(err.message);
      setSimulationState('running');
    }
  };

  const handleRecordToggle = async () => {
    try {
      if (isRecording) {
        // Stop recording
        const result = await api.simulation.stopRecording();
        setIsRecording(false);
        setRecordingDuration(0);
        console.log('Recording stopped:', result);
      } else {
        // Start recording
        const result = await api.simulation.startRecording({ format: 'json' });
        setIsRecording(true);
        setRecordingDuration(0);
        console.log('Recording started:', result);
      }
    } catch (err) {
      console.error('Recording error:', err);
      setError(err.message);
    }
  };

  const runningServicesCount = services?.filter(s => s.status === 'online').length || 0;
  const totalServices = services?.length || 0;

  const isPlayDisabled = simulationState === 'starting' || simulationState === 'running' || simulationState === 'stopping';
  const isStopDisabled = simulationState === 'stopped' || simulationState === 'stopping' || simulationState === 'starting';
  const isReplayDisabled = simulationState !== 'running';
  const isRecordDisabled = simulationState !== 'running';

  return (
    <div className="bg-slate-800 rounded-lg shadow-xl p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-blue-600 rounded-lg flex items-center justify-center">
            <Play className="w-6 h-6 text-white" />
          </div>
          <div>
            <h3 className="text-lg font-semibold text-white">Simulation Controls</h3>
            <p className="text-sm text-slate-400">
              Scenario: {currentScenario || 'None selected'}
            </p>
          </div>
        </div>
      </div>

      {/* Error Message */}
      {error && (
        <div className="mb-4 bg-red-900/50 border border-red-500 text-red-200 px-4 py-2 rounded-lg">
          {error}
        </div>
      )}

      {/* Control Buttons */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        {/* Play Button */}
        <button
          onClick={handleStart}
          disabled={isPlayDisabled}
          className={`
            flex flex-col items-center gap-2 px-6 py-4 rounded-lg transition-all font-semibold
            ${simulationState === 'starting'
              ? 'bg-green-600/50 cursor-wait'
              : isPlayDisabled
                ? 'bg-slate-700 opacity-50 cursor-not-allowed'
                : 'bg-green-600 hover:bg-green-700 active:scale-95'
            }
            ${!isPlayDisabled && 'hover:shadow-lg hover:shadow-green-600/50'}
          `}
          title={isPlayDisabled ? 'Simulation already running' : 'Start simulation'}
        >
          {simulationState === 'starting' ? (
            <Loader2 className="w-6 h-6 text-white animate-spin" />
          ) : (
            <Play className="w-6 h-6 text-white fill-current" />
          )}
          <span className="text-sm text-white">
            {simulationState === 'starting' ? 'Starting...' : 'Start'}
          </span>
        </button>

        {/* Stop Button */}
        <button
          onClick={handleStop}
          disabled={isStopDisabled}
          className={`
            flex flex-col items-center gap-2 px-6 py-4 rounded-lg transition-all font-semibold
            ${simulationState === 'stopping'
              ? 'bg-red-600/50 cursor-wait'
              : isStopDisabled
                ? 'bg-slate-700 opacity-50 cursor-not-allowed'
                : 'bg-red-600 hover:bg-red-700 active:scale-95'
            }
            ${!isStopDisabled && 'hover:shadow-lg hover:shadow-red-600/50'}
          `}
          title={isStopDisabled ? 'Simulation not running' : 'Stop simulation'}
        >
          {simulationState === 'stopping' ? (
            <Loader2 className="w-6 h-6 text-white animate-spin" />
          ) : (
            <Square className="w-6 h-6 text-white fill-current" />
          )}
          <span className="text-sm text-white">
            {simulationState === 'stopping' ? 'Stopping...' : 'Stop'}
          </span>
        </button>

        {/* Replay Button */}
        <button
          onClick={handleRestart}
          disabled={isReplayDisabled}
          className={`
            flex flex-col items-center gap-2 px-6 py-4 rounded-lg transition-all font-semibold
            ${isReplayDisabled
              ? 'bg-slate-700 opacity-50 cursor-not-allowed'
              : 'bg-amber-600 hover:bg-amber-700 active:scale-95 hover:shadow-lg hover:shadow-amber-600/50'
            }
          `}
          title={isReplayDisabled ? 'Start simulation first' : 'Restart simulation'}
        >
          <RotateCw className="w-6 h-6 text-white" />
          <span className="text-sm text-white">Restart</span>
        </button>

        {/* Record Button */}
        <button
          onClick={handleRecordToggle}
          disabled={isRecordDisabled}
          className={`
            flex flex-col items-center gap-2 px-6 py-4 rounded-lg transition-all font-semibold
            ${isRecording
              ? 'bg-red-600 hover:bg-red-700 animate-pulse'
              : isRecordDisabled
                ? 'bg-slate-700 opacity-50 cursor-not-allowed'
                : 'bg-slate-600 hover:bg-slate-700 active:scale-95'
            }
            ${isRecording && 'hover:shadow-lg hover:shadow-red-600/50'}
          `}
          title={isRecordDisabled ? 'Start simulation first' : isRecording ? 'Stop recording' : 'Start recording'}
        >
          <Circle className={`w-6 h-6 text-white ${isRecording ? 'fill-current' : ''}`} />
          <span className="text-sm text-white">
            {isRecording ? formatTime(recordingDuration) : 'Record'}
          </span>
        </button>
      </div>

      {/* Status Bar */}
      <div className="flex items-center justify-between p-4 bg-slate-900 rounded-lg">
        <div className="flex items-center gap-6">
          {/* State Indicator */}
          <div className="flex items-center gap-2">
            <div className={`
              w-2 h-2 rounded-full
              ${simulationState === 'running' ? 'bg-green-500 animate-pulse' :
                simulationState === 'starting' || simulationState === 'stopping' ? 'bg-yellow-500 animate-pulse' :
                'bg-slate-500'}
            `}></div>
            <span className="text-sm text-slate-300 font-medium capitalize">
              {simulationState}
            </span>
          </div>

          {/* Services */}
          <div className="flex items-center gap-2">
            <span className="text-sm text-slate-400">Services:</span>
            <span className={`text-sm font-semibold ${runningServicesCount === totalServices ? 'text-green-400' : 'text-yellow-400'}`}>
              {runningServicesCount}/{totalServices}
            </span>
          </div>

          {/* FPS */}
          {simulationState === 'running' && (
            <div className="flex items-center gap-2">
              <span className="text-sm text-slate-400">FPS:</span>
              <span className="text-sm font-semibold text-blue-400">
                {fps.toFixed(1)}
              </span>
            </div>
          )}
        </div>

        {/* Timer */}
        {simulationState === 'running' && (
          <div className="flex items-center gap-2">
            <span className="text-sm text-slate-400">Runtime:</span>
            <span className="text-lg font-mono font-semibold text-white">
              {formatTime(simulationTime)}
            </span>
          </div>
        )}
      </div>

      {/* Recording Status Badge */}
      {isRecording && (
        <div className="mt-4 flex items-center gap-3 px-4 py-3 bg-red-900/50 border border-red-700 rounded-lg">
          <div className="w-3 h-3 bg-red-500 rounded-full animate-pulse"></div>
          <span className="text-sm text-red-200 font-medium">
            Recording in progress • {formatTime(recordingDuration)}
          </span>
          <span className="text-xs text-red-300 ml-auto">
            Format: JSON
          </span>
        </div>
      )}
    </div>
  );
};

export default SimulationControls;
