import { useState, useEffect } from 'react';
import { X, Upload, MapPin, FileText, Loader } from 'lucide-react';
import { api } from '../api';

function ScenarioDialog({ isOpen, onClose, onSelectScenario }) {
  const [scenarios, setScenarios] = useState([]);
  const [selectedScenario, setSelectedScenario] = useState(null);
  const [scenarioDetails, setScenarioDetails] = useState({});
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (isOpen) {
      fetchScenarios();
    }
  }, [isOpen]);

  useEffect(() => {
    if (selectedScenario) {
      loadScenarioDetails(selectedScenario);
    }
  }, [selectedScenario]);

  const fetchScenarios = async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await api.getScenarios();
      if (response.success) {
        setScenarios(response.scenarios);
        // Load details for all scenarios immediately
        if (response.scenarios && response.scenarios.length > 0) {
          // Load all scenario details in parallel
          const detailPromises = response.scenarios.map(scenario => 
            loadScenarioDetails(scenario).catch(err => {
              console.error(`Error loading details for ${scenario}:`, err);
              return null;
            })
          );
          await Promise.all(detailPromises);
        }
      } else {
        setError('Failed to load scenarios');
      }
    } catch (err) {
      console.error('Error fetching scenarios:', err);
      setError('Failed to load scenarios');
    } finally {
      setLoading(false);
    }
  };

  const loadScenarioDetails = async (scenarioName) => {
    try {
      const response = await api.getScenario(scenarioName);
      if (response.success) {
        setScenarioDetails(prev => ({
          ...prev,
          [scenarioName]: response.config
        }));
      }
    } catch (err) {
      console.error('Error loading scenario details:', err);
    }
  };

  const handleFileUpload = async (event) => {
    const file = event.target.files[0];
    if (!file) return;

    if (!file.name.endsWith('.yaml') && !file.name.endsWith('.yml')) {
      setError('Please upload a YAML file (.yaml or .yml)');
      return;
    }

    try {
      setUploading(true);
      setError(null);

      const formData = new FormData();
      formData.append('scenario', file);

      const response = await fetch('/api/scenarios/upload', {
        method: 'POST',
        body: formData
      });

      if (!response.ok) {
        let errorMsg = 'Failed to upload scenario';
        try {
          const errorData = await response.json();
          errorMsg = errorData.error || errorMsg;
        } catch (e) {
          errorMsg = `Server error: ${response.status} ${response.statusText}`;
        }
        throw new Error(errorMsg);
      }

      const result = await response.json();

      if (result.success) {
        await fetchScenarios();
        setSelectedScenario(file.name);
        setError(null);
      } else {
        setError(result.error || 'Failed to upload scenario');
      }
    } catch (err) {
      console.error('Error uploading scenario:', err);
      setError('Failed to upload scenario: ' + err.message);
    } finally {
      setUploading(false);
      // Reset file input
      event.target.value = '';
    }
  };

  const handleSubmit = (e) => {
    if (e) {
      e.preventDefault();
      e.stopPropagation();
    }
    
    console.log('Submit button clicked. Selected scenario:', selectedScenario);
    console.log('onSelectScenario callback:', onSelectScenario);
    
    if (!selectedScenario) {
      console.warn('No scenario selected');
      setError('Please select a scenario first');
      return;
    }
    
    try {
      if (onSelectScenario && typeof onSelectScenario === 'function') {
        console.log('Calling onSelectScenario with:', selectedScenario);
        onSelectScenario(selectedScenario);
      } else {
        console.error('onSelectScenario is not a function or is undefined');
        setError('Error: Scenario selection handler not available');
        return;
      }
      
      console.log('Closing dialog');
      onClose();
    } catch (err) {
      console.error('Error in handleSubmit:', err);
      setError('Failed to select scenario: ' + err.message);
    }
  };

  const handleCancel = () => {
    setSelectedScenario(null);
    setError(null);
    onClose();
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

  const getTownThumbnail = (town) => {
    // Generate a simple thumbnail based on town name
    // In a real implementation, you might want to fetch actual map previews from CARLA
    const townColors = {
      'Town01': 'bg-blue-500',
      'Town02': 'bg-green-500',
      'Town03': 'bg-purple-500',
      'Town04': 'bg-yellow-500',
      'Town05': 'bg-red-500',
      'Town10': 'bg-indigo-500',
    };
    return townColors[town] || 'bg-slate-500';
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-75 flex items-center justify-center z-50 p-4">
      <div className="bg-slate-900 rounded-xl shadow-2xl border border-slate-700 w-full max-w-5xl max-h-[90vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="px-6 py-4 border-b border-slate-700 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <MapPin className="w-6 h-6 text-blue-500" />
            <h2 className="text-2xl font-bold text-white">Select Scenario</h2>
          </div>
          <button
            onClick={handleCancel}
            className="text-slate-400 hover:text-white transition-colors p-2 hover:bg-slate-800 rounded-lg"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Error Display */}
        {error && (
          <div className="mx-6 mt-4 bg-red-900/30 border border-red-700/50 text-red-200 px-4 py-3 rounded-lg">
            <p className="text-sm font-medium">{error}</p>
          </div>
        )}

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <Loader className="w-8 h-8 animate-spin text-blue-500" />
              <span className="ml-3 text-slate-400">Loading scenarios...</span>
            </div>
          ) : scenarios.length === 0 ? (
            <div className="text-center py-12">
              <FileText className="w-16 h-16 mx-auto text-slate-500 mb-4" />
              <p className="text-slate-400 mb-2">No scenarios found</p>
              <p className="text-sm text-slate-500">Upload a scenario file to get started</p>
            </div>
          ) : (
            <div className="space-y-4">
              {scenarios.map((scenario) => {
                const details = scenarioDetails[scenario];
                // Try multiple locations for town (different YAML structures)
                const town = details?.carla?.town || 
                            details?.world?.town || 
                            details?.town || 
                            'Unknown';
                const mode = details?.mode || 'Unknown';
                const description = details?.description || 
                  `${getModeLabel(mode)} - ${town}`;
                
                // Get thumbnail image path
                const thumbnailPath = town !== 'Unknown' 
                  ? `/scenarios/thumbnail/${town}.jpg`
                  : null;

                return (
                  <div
                    key={scenario}
                    onClick={() => {
                      console.log('Scenario clicked:', scenario);
                      setSelectedScenario(scenario);
                    }}
                    className={`
                      cursor-pointer rounded-lg border-2 transition-all
                      ${selectedScenario === scenario
                        ? 'border-blue-500 bg-blue-900/20'
                        : 'border-slate-700 bg-slate-800 hover:border-slate-600 hover:bg-slate-800/80'
                      }
                    `}
                  >
                    <div className="flex p-4">
                      {/* Thumbnail */}
                      <div className="flex-shrink-0 w-32 h-32 mr-4">
                        {details ? (
                          thumbnailPath ? (
                            <div className="w-full h-full rounded-lg overflow-hidden shadow-lg bg-slate-700">
                              <img
                                src={thumbnailPath}
                                alt={`${town} thumbnail`}
                                className="w-full h-full object-cover"
                                onError={(e) => {
                                  // Fallback to colored box if image fails to load
                                  e.target.style.display = 'none';
                                  e.target.parentElement.className = `w-full h-full rounded-lg flex items-center justify-center ${getTownThumbnail(town)} shadow-lg`;
                                  e.target.parentElement.innerHTML = `<span class="text-white font-bold text-lg">${town}</span>`;
                                }}
                              />
                            </div>
                          ) : (
                            <div className={`
                              w-full h-full rounded-lg flex items-center justify-center
                              ${getTownThumbnail(town)}
                              shadow-lg
                            `}>
                              <span className="text-white font-bold text-lg">{town}</span>
                            </div>
                          )
                        ) : (
                          <div className="w-full h-full rounded-lg flex items-center justify-center bg-slate-700 shadow-lg">
                            <Loader className="w-6 h-6 animate-spin text-slate-400" />
                          </div>
                        )}
                      </div>

                      {/* Description */}
                      <div className="flex-1 min-w-0">
                        <h3 className="text-lg font-semibold text-white mb-2 truncate">
                          {scenario}
                        </h3>
                        <p className="text-sm text-slate-400 mb-2">
                          {description}
                        </p>
                        {details && (
                          <div className="flex flex-wrap gap-2 mt-2">
                            <span className="px-2 py-1 bg-slate-700 rounded text-xs text-slate-300">
                              {getModeLabel(mode)}
                            </span>
                            {details.carla && (
                              <span className="px-2 py-1 bg-slate-700 rounded text-xs text-slate-300">
                                Port: {details.carla.port}
                              </span>
                            )}
                            {details.sumo && details.sumo.enabled && (
                              <span className="px-2 py-1 bg-slate-700 rounded text-xs text-slate-300">
                                SUMO Enabled
                              </span>
                            )}
                            {details.artery && details.artery.enabled && (
                              <span className="px-2 py-1 bg-slate-700 rounded text-xs text-slate-300">
                                Artery Enabled
                              </span>
                            )}
                          </div>
                        )}
                      </div>

                      {/* Selection Indicator */}
                      {selectedScenario === scenario && (
                        <div className="flex-shrink-0 ml-4 flex items-center">
                          <div className="w-6 h-6 rounded-full bg-blue-500 flex items-center justify-center">
                            <div className="w-3 h-3 rounded-full bg-white"></div>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-slate-700 flex items-center justify-between">
          <div className="flex items-center space-x-4">
            <label className="flex items-center space-x-2 px-4 py-2 bg-slate-800 hover:bg-slate-700 rounded-lg cursor-pointer transition-colors">
              <Upload className="w-4 h-4" />
              <span className="text-sm">
                {uploading ? 'Uploading...' : 'Upload Scenario'}
              </span>
              <input
                type="file"
                accept=".yaml,.yml"
                onChange={handleFileUpload}
                disabled={uploading}
                className="hidden"
              />
            </label>
          </div>

          <div className="flex items-center space-x-3">
            <button
              type="button"
              onClick={handleCancel}
              className="px-6 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors text-white"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleSubmit}
              disabled={!selectedScenario}
              className={`
                px-6 py-2 rounded-lg transition-colors font-medium
                ${selectedScenario
                  ? 'bg-blue-600 hover:bg-blue-700 text-white cursor-pointer'
                  : 'bg-slate-700 opacity-50 cursor-not-allowed text-slate-400'
                }
              `}
            >
              Load Scenario
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default ScenarioDialog;

