import { useState, useEffect } from 'react';
import { MapPin, Square, RefreshCw, Loader, Info } from 'lucide-react';
import { api } from '../api';

function ScenarioSelector({ onScenarioChange }) {
  const [scenarios, setScenarios] = useState([]);
  const [selectedScenario, setSelectedScenario] = useState('');
  const [currentScenario, setCurrentScenario] = useState(null);
  const [scenarioDetails, setScenarioDetails] = useState(null);
  const [loading, setLoading] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetchScenarios();
    loadCurrentScenario();

    // Poll for current scenario status every 10 seconds
    const interval = setInterval(loadCurrentScenario, 10000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (selectedScenario) {
      loadScenarioDetails(selectedScenario);
    }
  }, [selectedScenario]);

  const fetchScenarios = async () => {
    try {
      setLoading(true);
      const response = await api.getScenarios();
      if (response.success) {
        setScenarios(response.scenarios);
        if (response.scenarios.length > 0 && !selectedScenario) {
          const firstScenario = response.scenarios[0];
          setSelectedScenario(firstScenario);
          // Notify parent of auto-selected scenario
          if (onScenarioChange) {
            onScenarioChange(firstScenario);
          }
        }
      }
    } catch (err) {
      console.error('Error fetching scenarios:', err);
      setError('Failed to load scenarios');
    } finally {
      setLoading(false);
    }
  };

  const loadCurrentScenario = async () => {
    try {
      const response = await api.getCurrentScenario();
      if (response.success) {
        setCurrentScenario(response.scenario);
      }
    } catch (err) {
      console.error('Error loading current scenario:', err);
    }
  };

  const loadScenarioDetails = async (scenarioName) => {
    try {
      const response = await api.getScenario(scenarioName);
      if (response.success) {
        setScenarioDetails(response.config);
      }
    } catch (err) {
      console.error('Error loading scenario details:', err);
    }
  };

  const handleScenarioChange = (scenarioName) => {
    setSelectedScenario(scenarioName);
    // Notify parent component of scenario change
    if (onScenarioChange) {
      onScenarioChange(scenarioName);
    }
  };

  const handleStopScenario = async () => {
    setStopping(true);
    setError(null);

    try {
      const response = await api.stopScenario();
      if (response.success) {
        setCurrentScenario(null);
      } else {
        setError(response.error || 'Failed to stop scenario');
      }
    } catch (err) {
      setError('Failed to stop scenario: ' + err.message);
      console.error('Error stopping scenario:', err);
    } finally {
      setStopping(false);
    }
  };

  const getModeLabel = (mode) => {
    switch (mode) {
      case 'sumo_cosim':
        return 'SUMO Co-Simulation';
      case 'carla_scenario':
        return 'Carla Scenario Runner';
      default:
        return mode || 'Unknown';
    }
  };

  return (
    <div className="bg-slate-800 rounded-lg p-6 border border-slate-700">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center space-x-2">
          <MapPin className="w-5 h-5 text-blue-500" />
          <h2 className="text-lg font-semibold">Scenario Management</h2>
        </div>
        <button
          onClick={fetchScenarios}
          className="p-2 text-slate-400 hover:text-white hover:bg-slate-700 rounded"
          disabled={loading}
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      <div className="space-y-4">
        {/* Current Running Scenario */}
        {currentScenario && (
          <div className="bg-green-900/20 border border-green-700 rounded-lg p-4">
            <div className="flex items-start justify-between">
              <div className="flex-1">
                <h3 className="font-medium text-green-400 mb-2">Currently Running</h3>
                <div className="space-y-1 text-sm">
                  <p className="text-slate-300">
                    <span className="text-slate-500">Scenario:</span> <span className="font-semibold">{currentScenario.scenario}</span>
                  </p>
                  <p className="text-slate-300">
                    <span className="text-slate-500">Mode:</span> {getModeLabel(currentScenario.mode)}
                  </p>
                  <p className="text-slate-300">
                    <span className="text-slate-500">Town:</span> {currentScenario.town}
                  </p>
                  <p className="text-xs text-slate-500 mt-2">
                    Started: {new Date(currentScenario.startedAt).toLocaleString()}
                  </p>
                </div>
              </div>
              <button
                onClick={handleStopScenario}
                disabled={stopping}
                className="flex items-center gap-2 px-4 py-2 bg-red-600 hover:bg-red-700 rounded-lg transition-colors disabled:opacity-50"
              >
                {stopping ? (
                  <Loader className="w-4 h-4 animate-spin" />
                ) : (
                  <Square className="w-4 h-4" />
                )}
                Stop
              </button>
            </div>
          </div>
        )}

        {/* Error Message */}
        {error && (
          <div className="bg-red-900/20 border border-red-700 rounded-lg p-4">
            <p className="text-sm text-red-400">{error}</p>
          </div>
        )}

        {/* Scenario Dropdown */}
        <div>
          <label className="block text-sm text-slate-400 mb-2">
            Select Scenario Configuration
          </label>
          <select
            value={selectedScenario}
            onChange={(e) => handleScenarioChange(e.target.value)}
            className="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-blue-500"
            disabled={loading}
          >
            <option value="">-- Choose a scenario --</option>
            {scenarios.map((scenario) => (
              <option key={scenario} value={scenario}>
                {scenario}
              </option>
            ))}
          </select>
        </div>

        {/* Scenario Details */}
        {scenarioDetails && (
          <div className="bg-slate-900 rounded-lg p-4 space-y-3">
            <div className="flex items-center space-x-2 text-sm text-slate-400 mb-2">
              <Info className="w-4 h-4 text-blue-500" />
              <span>Configuration Details</span>
            </div>

            {scenarioDetails.mode && (
              <div className="pb-2 border-b border-slate-700">
                <p className="text-xs text-slate-500 uppercase mb-1">Mode</p>
                <p className="text-sm font-semibold text-blue-400">
                  {getModeLabel(scenarioDetails.mode)}
                </p>
              </div>
            )}

            {scenarioDetails.carla && (
              <div>
                <p className="text-xs text-slate-500 uppercase mb-1">Carla</p>
                <p className="text-sm text-white">
                  Town: <span className="font-semibold">{scenarioDetails.carla.town}</span>
                  {' | '}
                  Port: <span className="font-semibold">{scenarioDetails.carla.port}</span>
                  {' | '}
                  Quality: <span className="font-semibold">{scenarioDetails.carla.quality}</span>
                </p>
                {scenarioDetails.carla.resolution && (
                  <p className="text-sm text-slate-400">
                    Resolution: {scenarioDetails.carla.resolution.width}x{scenarioDetails.carla.resolution.height}
                  </p>
                )}
              </div>
            )}

            {scenarioDetails.mode === 'sumo_cosim' && scenarioDetails.sumo && (
              <div>
                <p className="text-xs text-slate-500 uppercase mb-1">SUMO</p>
                <p className="text-sm text-white">
                  Port: <span className="font-semibold">{scenarioDetails.sumo.port}</span>
                  {' | '}
                  Clients: <span className="font-semibold">{scenarioDetails.sumo.num_clients}</span>
                </p>
              </div>
            )}

            {scenarioDetails.mode === 'carla_scenario' && scenarioDetails.scenario_runner && (
              <div>
                <p className="text-xs text-slate-500 uppercase mb-1">Scenario Runner</p>
                <p className="text-sm text-slate-400 truncate" title={scenarioDetails.scenario_runner.scenario_file}>
                  File: {scenarioDetails.scenario_runner.scenario_file}
                </p>
                <p className="text-sm text-slate-400">
                  Timeout: {scenarioDetails.scenario_runner.timeout}s
                </p>
              </div>
            )}
          </div>
        )}

        <p className="text-xs text-slate-500 text-center mt-4">
          Select a scenario to use with the Simulation Player controls below
        </p>
      </div>
    </div>
  );
}

export default ScenarioSelector;
