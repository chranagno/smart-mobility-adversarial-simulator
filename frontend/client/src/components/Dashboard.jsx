import { useState, useEffect } from 'react';
import { RefreshCw, Play, Square, Server, Camera, Map, MapPin, Gamepad2 } from 'lucide-react';
import ServiceCard from './ServiceCard';
import LogViewer from './LogViewer';
import SystemStatus from './SystemStatus';
import ScenarioDialog from './ScenarioDialog';
import SimulationControls from './SimulationControls';
import CarlaImageViewer from './CarlaImageViewer';
import VehicleMap from './VehicleMap';
import { api } from '../api';

function Dashboard({ services, loading, error, onRefresh }) {
  const [selectedService, setSelectedService] = useState(null);
  const [showImageViewer, setShowImageViewer] = useState(false);
  const [showVehicleMap, setShowVehicleMap] = useState(false);
  const [isStartingAll, setIsStartingAll] = useState(false);
  const [isStoppingAll, setIsStoppingAll] = useState(false);
  const [selectedScenario, setSelectedScenario] = useState(null);
  const [currentRunningScenario, setCurrentRunningScenario] = useState(null);
  const [simulationStatus, setSimulationStatus] = useState(null);
  const [showScenarioDialog, setShowScenarioDialog] = useState(false);
  const [isStartingTeleoperation, setIsStartingTeleoperation] = useState(false);

  const isLocalFrontend = (() => {
    const hostname = window.location.hostname;
    return hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '::1' || hostname.startsWith('127.');
  })();
  const canLaunchTeleoperation = isLocalFrontend && simulationStatus?.state === 'running';

  // Load current running scenario and simulation status on mount
  useEffect(() => {
    loadCurrentScenario();
    loadSimulationStatus();

    // Poll for simulation status every 2 seconds
    const interval = setInterval(() => {
      loadSimulationStatus();
    }, 2000);

    return () => clearInterval(interval);
  }, []);

  const loadCurrentScenario = async () => {
    try {
      const response = await api.getCurrentScenario();
      if (response.success && response.scenario) {
        setCurrentRunningScenario(response.scenario.scenario);
      } else {
        setCurrentRunningScenario(null);
      }
    } catch (err) {
      console.error('Error loading current scenario:', err);
    }
  };

  const loadSimulationStatus = async () => {
    try {
      const response = await api.simulation.getStatus();
      if (response && response.success) {
        setSimulationStatus(response);
        // Update selected scenario from status if available
        if (response.scenario && !selectedScenario) {
          setSelectedScenario(response.scenario);
        }
      }
    } catch (err) {
      console.error('Error loading simulation status:', err);
      // Don't show error to user for background polling
    }
  };

  const handleScenarioSelect = async (scenario) => {
    setSelectedScenario(scenario);

    // Actually load the scenario when submit is clicked
    try {
      const response = await api.simulation.load(scenario);
      if (response.success) {
        console.log('Scenario loaded successfully:', scenario);
        // Refresh services and status after loading
        setTimeout(() => {
          onRefresh();
          loadSimulationStatus();
        }, 2000);
      } else {
        console.error('Failed to load scenario:', response.error);
      }
    } catch (err) {
      console.error('Error loading scenario:', err);
    }
  };

  const handleStartAll = async (scenario = null) => {
    setIsStartingAll(true);
    try {
      const response = await api.startAll(scenario);
      if (response.success) {
        console.log('All services started', scenario ? `with scenario: ${scenario}` : '');
        setTimeout(onRefresh, 2000); // Wait a bit for services to register
      } else {
        console.error('Failed to start all services:', response.error);
      }
    } catch (err) {
      console.error('Error starting all services:', err);
    } finally {
      setIsStartingAll(false);
    }
  };

  const handleStopAll = async () => {
    setIsStoppingAll(true);
    try {
      const response = await api.stopAll();
      if (response.success) {
        console.log('All services stopped');
        onRefresh();
      } else {
        console.error('Failed to stop all services:', response.error);
      }
    } catch (err) {
      console.error('Error stopping all services:', err);
    } finally {
      setIsStoppingAll(false);
    }
  };

  const handleLaunchTeleoperation = async () => {
    if (!isLocalFrontend) {
      alert('Teleoperation can only be launched from the local machine. Open the UI at http://localhost:5173.');
      return;
    }
    if (simulationStatus?.state !== 'running') {
      alert('Teleoperation is available only while the simulation is running.');
      return;
    }

    setIsStartingTeleoperation(true);
    try {
      const result = await api.teleoperation.start({
        host: '127.0.0.1',
        port: 2000,
        observeOnly: true
      });

      if (result.success) {
        console.log('[Dashboard] Teleoperation GUI started successfully');
      } else {
        console.error('[Dashboard] Failed to start teleoperation:', result.error);
        alert(`Failed to start teleoperation: ${result.error || 'Unknown error'}`);
      }
    } catch (err) {
      console.error('[Dashboard] Error starting teleoperation:', err);
      alert(`Error starting teleoperation: ${err.message || 'Unknown error'}`);
    } finally {
      setIsStartingTeleoperation(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-900">
      {/* Header */}
      <header className="bg-slate-800 border-b border-slate-700">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-3">
              <Server className="w-8 h-8 text-blue-500" />
              <h1 className="text-2xl font-bold text-white">Simulator Control Panel</h1>
            </div>
            <div className="flex items-center space-x-3">
              <button
                onClick={() => setShowVehicleMap(true)}
                className="flex items-center space-x-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors"
                title="Open Vehicle Map"
              >
                <Map className="w-4 h-4" />
                <span>Vehicle Map</span>
              </button>
              {/* <button
                onClick={() => setShowImageViewer(true)}
                className="flex items-center space-x-2 px-4 py-2 bg-purple-600 hover:bg-purple-700 rounded-lg transition-colors"
                title="Open Carla Camera"
              >
                <Camera className="w-4 h-4" />
                <span>View Camera</span>
              </button> */}
              <button
                onClick={handleLaunchTeleoperation}
                disabled={isStartingTeleoperation || !canLaunchTeleoperation}
                className="flex items-center space-x-2 px-4 py-2 bg-green-600 hover:bg-green-700 rounded-lg transition-colors disabled:opacity-50"
                title={
                  !isLocalFrontend
                    ? 'Teleoperation is local-only'
                    : simulationStatus?.state !== 'running'
                      ? 'Teleoperation is available only while the simulation is running'
                      : 'Launch Teleoperation Interface'
                }
              >
                <Gamepad2 className="w-4 h-4" />
                <span>{isStartingTeleoperation ? 'Launching...' : 'Teleoperation'}</span>
              </button>
              <button
                onClick={onRefresh}
                disabled={loading}
                className="flex items-center space-x-2 px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors disabled:opacity-50"
              >
                <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
                <span>Refresh</span>
              </button>
            </div>
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* System Status */}
        <SystemStatus services={services} />

        {/* Scenario Status Display */}
        {simulationStatus && (
          <div className="mt-6 bg-slate-800 rounded-lg p-6 border border-slate-700">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-3">
                <MapPin className="w-5 h-5 text-blue-500" />
                <div>
                  <h2 className="text-lg font-semibold text-white">Scenario Status</h2>
                  <p className="text-sm text-slate-400">
                    {simulationStatus.scenario
                      ? `Current: ${simulationStatus.scenario}`
                      : 'No scenario loaded'}
                  </p>
                </div>
              </div>
              <div className="flex items-center space-x-2">
                <div className={`
                  w-3 h-3 rounded-full
                  ${simulationStatus.state === 'running' ? 'bg-green-500 animate-pulse' :
                    simulationStatus.state === 'loaded' ? 'bg-blue-500' :
                      simulationStatus.state === 'loading' || simulationStatus.state === 'starting' ? 'bg-yellow-500 animate-pulse' :
                        simulationStatus.state === 'idle' ? 'bg-slate-500' :
                          'bg-slate-500'}
                `}></div>
                <span className="text-slate-300 capitalize text-sm font-medium">
                  {simulationStatus.state || 'idle'}
                </span>
              </div>
            </div>
          </div>
        )}

        {/* Simulation Controls */}
        <div className="mt-6">
          <SimulationControls
            api={api}
            currentScenario={selectedScenario}
            services={services}
            simulationStatus={simulationStatus}
            onRefresh={() => {
              onRefresh();
              loadSimulationStatus();
            }}
            onLoadScenario={() => setShowScenarioDialog(true)}
          />
        </div>

        {/* Quick Actions */}
        <div className="mt-6 bg-slate-800 rounded-lg p-6 border border-slate-700">
          <h2 className="text-lg font-semibold mb-4">Quick Actions</h2>
          <div className="flex space-x-4">
            <button
              onClick={handleStartAll}
              disabled={isStartingAll}
              className="flex items-center space-x-2 px-6 py-3 bg-green-600 hover:bg-green-700 rounded-lg transition-colors disabled:opacity-50"
            >
              <Play className="w-5 h-5" />
              <span>{isStartingAll ? 'Starting...' : 'Start All Services'}</span>
            </button>
            <button
              onClick={handleStopAll}
              disabled={isStoppingAll}
              className="flex items-center space-x-2 px-6 py-3 bg-red-600 hover:bg-red-700 rounded-lg transition-colors disabled:opacity-50"
            >
              <Square className="w-5 h-5" />
              <span>{isStoppingAll ? 'Stopping...' : 'Stop All Services'}</span>
            </button>
          </div>
        </div>

        {/* Error Display */}
        {error && (
          <div className="mt-6 bg-red-900/50 border border-red-700 rounded-lg p-4">
            <p className="text-red-200">Error: {error}</p>
          </div>
        )}

        {/* Services Grid */}
        <div className="mt-6">
          <h2 className="text-lg font-semibold mb-4">Services</h2>
          {loading && services.length === 0 ? (
            <div className="text-center py-12">
              <RefreshCw className="w-12 h-12 animate-spin mx-auto text-slate-500" />
              <p className="mt-4 text-slate-400">Loading services...</p>
            </div>
          ) : services.length === 0 ? (
            <div className="text-center py-12 bg-slate-800 rounded-lg border border-slate-700">
              <Server className="w-12 h-12 mx-auto text-slate-500" />
              <p className="mt-4 text-slate-400">No services running</p>
              <p className="text-sm text-slate-500 mt-2">Start services using the Quick Actions above</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {services.map((service) => (
                <ServiceCard
                  key={service.pm_id}
                  service={service}
                  onViewLogs={() => setSelectedService(service)}
                  onRefresh={onRefresh}
                />
              ))}
            </div>
          )}
        </div>

        {/* Log Viewer Modal */}
        {selectedService && (
          <LogViewer
            service={selectedService}
            onClose={() => setSelectedService(null)}
          />
        )}

        {/* Vehicle Map Modal */}
        {showVehicleMap && (
          <div className="fixed inset-0 bg-black bg-opacity-75 flex items-center justify-center z-50 p-4">
            <div className="bg-slate-900 rounded-lg max-w-7xl w-full max-h-[90vh] overflow-auto">
              <div className="p-4 border-b border-slate-700 flex items-center justify-between">
                <h2 className="text-xl font-semibold text-white">Vehicle Map</h2>
                <button
                  onClick={() => setShowVehicleMap(false)}
                  className="text-slate-400 hover:text-white transition-colors"
                >
                  ✕
                </button>
              </div>
              <div className="p-4">
                <VehicleMap />
              </div>
            </div>
          </div>
        )}

        {/* Carla Image Viewer Modal */}
        {showImageViewer && (
          <CarlaImageViewer
            onClose={() => setShowImageViewer(false)}
          />
        )}

        {/* Scenario Dialog */}
        <ScenarioDialog
          isOpen={showScenarioDialog}
          onClose={() => setShowScenarioDialog(false)}
          onSelectScenario={handleScenarioSelect}
        />
      </div>
    </div>
  );
}

export default Dashboard;
