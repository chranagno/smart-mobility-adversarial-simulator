import { useState, useEffect } from 'react';
import { X, Camera, RefreshCw, Download, Car, Eye, Video, Image as ImageIcon } from 'lucide-react';
import { api } from '../api';

function CarlaImageViewer({ onClose }) {
  const [imageUrl, setImageUrl] = useState(null);
  const [vehicles, setVehicles] = useState([]);
  const [selectedVehicle, setSelectedVehicle] = useState(null);
  const [viewMode, setViewMode] = useState('spectator'); // 'spectator' or 'vehicle'
  const [captureMode, setCaptureMode] = useState('image'); // 'image' or 'video'
  const [capturing, setCapturing] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(null);

  // Camera settings
  const [width, setWidth] = useState(800);
  const [height, setHeight] = useState(600);
  const [quality, setQuality] = useState(85);
  const [fps, setFps] = useState(10);

  useEffect(() => {
    checkHealth();
    loadVehicles();
    
    // Refresh vehicle list every 5 seconds while viewer is open
    const interval = setInterval(() => {
      if (viewMode === 'vehicle') {
        loadVehicles();
      }
    }, 5000);
    
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    let interval;
    if (autoRefresh) {
      interval = setInterval(() => {
        captureImage();
      }, 2000); // Refresh every 2 seconds
    }
    return () => {
      if (interval) clearInterval(interval);
    };
  }, [autoRefresh, viewMode, selectedVehicle, width, height, quality]);

  const checkHealth = async () => {
    try {
      const result = await api.carla.checkHealth();
      setHealth(result);
    } catch (err) {
      setError('Image capture service unavailable');
    }
  };

  const loadVehicles = async () => {
    try {
      setError(null);
      const result = await api.carla.getVehicles();
      if (result && result.vehicles) {
        setVehicles(result.vehicles);
        if (result.vehicles.length > 0) {
          // Only set selected vehicle if one isn't already selected or if the current selection doesn't exist
          if (!selectedVehicle || !result.vehicles.find(v => v.id === selectedVehicle)) {
            setSelectedVehicle(result.vehicles[0].id);
          }
        } else {
          setError('No vehicles found. Make sure the simulation is running and vehicles have been spawned.');
        }
      } else {
        setError('Invalid response from vehicle list API');
      }
    } catch (err) {
      console.error('Failed to load vehicles:', err);
      setError(`Failed to load vehicles: ${err.message}. Make sure the image-capture service is running.`);
    }
  };

  const captureImage = async () => {
    setCapturing(true);
    setError(null);
    setStreaming(false);

    try {
      const options = { width, height, quality };
      let url;

      if (viewMode === 'spectator') {
        url = await api.carla.captureImage(options);
      } else if (viewMode === 'vehicle' && selectedVehicle) {
        url = await api.carla.captureVehicleImage(selectedVehicle, options);
      }

      // Add timestamp to force refresh
      setImageUrl(`${url}&t=${Date.now()}`);
    } catch (err) {
      setError('Failed to capture image');
      console.error(err);
    } finally {
      setCapturing(false);
    }
  };

  const startVideoStream = () => {
    setError(null);
    setStreaming(true);

    const options = { width, height, fps };
    const params = new URLSearchParams(options).toString();
    let url;

    if (viewMode === 'spectator') {
      url = `/api/carla/stream?${params}`;
    } else if (viewMode === 'vehicle' && selectedVehicle) {
      url = `/api/carla/stream/vehicle/${selectedVehicle}?${params}`;
    }

    setImageUrl(`${url}&t=${Date.now()}`);
  };

  const stopVideoStream = () => {
    setStreaming(false);
    setImageUrl(null);
  };

  const downloadImage = () => {
    if (!imageUrl) return;

    const link = document.createElement('a');
    link.href = imageUrl;
    link.download = `carla_${viewMode}_${Date.now()}.jpg`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-slate-800 rounded-lg w-full max-w-6xl max-h-[90vh] flex flex-col border border-slate-700">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-slate-700">
          <div className="flex items-center space-x-3">
            <Camera className="w-6 h-6 text-blue-500" />
            <h3 className="text-xl font-semibold text-white">Carla Camera View</h3>
            {health && health.connected && (
              <span className="px-2 py-1 bg-green-900/50 text-green-400 text-xs rounded">
                Connected
              </span>
            )}
            {health && !health.connected && (
              <span className="px-2 py-1 bg-red-900/50 text-red-400 text-xs rounded">
                Not Connected
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            className="p-2 hover:bg-slate-700 rounded transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Controls */}
        <div className="p-4 border-b border-slate-700 space-y-4">
          {/* Capture Mode */}
          <div className="flex items-center space-x-4">
            <label className="text-sm text-slate-400 w-24">Mode:</label>
            <div className="flex space-x-2">
              <button
                onClick={() => { setCaptureMode('image'); stopVideoStream(); }}
                className={`flex items-center space-x-2 px-4 py-2 rounded transition-colors ${
                  captureMode === 'image'
                    ? 'bg-purple-600 text-white'
                    : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                }`}
              >
                <ImageIcon className="w-4 h-4" />
                <span>Image</span>
              </button>
              <button
                onClick={() => setCaptureMode('video')}
                className={`flex items-center space-x-2 px-4 py-2 rounded transition-colors ${
                  captureMode === 'video'
                    ? 'bg-purple-600 text-white'
                    : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                }`}
              >
                <Video className="w-4 h-4" />
                <span>Video Stream</span>
              </button>
            </div>
          </div>

          {/* View Mode */}
          <div className="flex items-center space-x-4">
            <label className="text-sm text-slate-400 w-24">View:</label>
            <div className="flex space-x-2">
              <button
                onClick={() => setViewMode('spectator')}
                className={`flex items-center space-x-2 px-4 py-2 rounded transition-colors ${
                  viewMode === 'spectator'
                    ? 'bg-blue-600 text-white'
                    : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                }`}
              >
                <Eye className="w-4 h-4" />
                <span>Spectator</span>
              </button>
              <button
                onClick={() => setViewMode('vehicle')}
                className={`flex items-center space-x-2 px-4 py-2 rounded transition-colors ${
                  viewMode === 'vehicle'
                    ? 'bg-blue-600 text-white'
                    : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                }`}
                disabled={vehicles.length === 0}
              >
                <Car className="w-4 h-4" />
                <span>Vehicle</span>
              </button>
            </div>
          </div>

          {/* Vehicle Selector */}
          {viewMode === 'vehicle' && vehicles.length > 0 && (
            <div className="flex items-center space-x-4">
              <label className="text-sm text-slate-400 w-24">Vehicle:</label>
              <select
                value={selectedVehicle || ''}
                onChange={(e) => setSelectedVehicle(parseInt(e.target.value))}
                className="flex-1 px-3 py-2 bg-slate-700 border border-slate-600 rounded text-white focus:outline-none focus:border-blue-500"
              >
                {vehicles.map((vehicle) => (
                  <option key={vehicle.id} value={vehicle.id}>
                    ID: {vehicle.id} - {vehicle.type}
                  </option>
                ))}
              </select>
              <button
                onClick={loadVehicles}
                className="px-3 py-2 bg-slate-700 hover:bg-slate-600 rounded transition-colors"
                title="Refresh vehicle list"
              >
                <RefreshCw className="w-4 h-4" />
              </button>
            </div>
          )}

          {/* Camera Settings */}
          <div className="grid grid-cols-4 gap-4">
            <div>
              <label className="text-xs text-slate-400 mb-1 block">Width</label>
              <input
                type="number"
                value={width}
                onChange={(e) => setWidth(parseInt(e.target.value))}
                className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded text-white focus:outline-none focus:border-blue-500"
                min="320"
                max="1920"
                step="160"
              />
            </div>
            <div>
              <label className="text-xs text-slate-400 mb-1 block">Height</label>
              <input
                type="number"
                value={height}
                onChange={(e) => setHeight(parseInt(e.target.value))}
                className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded text-white focus:outline-none focus:border-blue-500"
                min="240"
                max="1080"
                step="120"
              />
            </div>
            {captureMode === 'image' && (
              <div>
                <label className="text-xs text-slate-400 mb-1 block">Quality</label>
                <input
                  type="number"
                  value={quality}
                  onChange={(e) => setQuality(parseInt(e.target.value))}
                  className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded text-white focus:outline-none focus:border-blue-500"
                  min="1"
                  max="100"
                />
              </div>
            )}
            {captureMode === 'video' && (
              <div>
                <label className="text-xs text-slate-400 mb-1 block">FPS</label>
                <input
                  type="number"
                  value={fps}
                  onChange={(e) => setFps(parseInt(e.target.value))}
                  className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded text-white focus:outline-none focus:border-blue-500"
                  min="1"
                  max="30"
                />
              </div>
            )}
          </div>

          {/* Action Buttons */}
          <div className="flex items-center space-x-4">
            {captureMode === 'image' && (
              <>
                <button
                  onClick={captureImage}
                  disabled={capturing || !health?.connected}
                  className="flex items-center space-x-2 px-6 py-2 bg-blue-600 hover:bg-blue-700 rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <Camera className="w-4 h-4" />
                  <span>{capturing ? 'Capturing...' : 'Capture Image'}</span>
                </button>

                <label className="flex items-center space-x-2 text-sm text-slate-300">
                  <input
                    type="checkbox"
                    checked={autoRefresh}
                    onChange={(e) => setAutoRefresh(e.target.checked)}
                    className="rounded"
                  />
                  <span>Auto-refresh (2s)</span>
                </label>
              </>
            )}

            {captureMode === 'video' && (
              <>
                {!streaming ? (
                  <button
                    onClick={startVideoStream}
                    disabled={!health?.connected}
                    className="flex items-center space-x-2 px-6 py-2 bg-red-600 hover:bg-red-700 rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <Video className="w-4 h-4" />
                    <span>Start Stream</span>
                  </button>
                ) : (
                  <button
                    onClick={stopVideoStream}
                    className="flex items-center space-x-2 px-6 py-2 bg-gray-600 hover:bg-gray-700 rounded transition-colors"
                  >
                    <X className="w-4 h-4" />
                    <span>Stop Stream</span>
                  </button>
                )}
                {streaming && (
                  <span className="flex items-center space-x-2 text-green-400 text-sm">
                    <div className="w-2 h-2 bg-green-400 rounded-full animate-pulse"></div>
                    <span>Live @ {fps} FPS</span>
                  </span>
                )}
              </>
            )}

            {imageUrl && !streaming && captureMode === 'image' && (
              <button
                onClick={downloadImage}
                className="flex items-center space-x-2 px-4 py-2 bg-green-600 hover:bg-green-700 rounded transition-colors"
              >
                <Download className="w-4 h-4" />
                <span>Download</span>
              </button>
            )}
          </div>
        </div>

        {/* Image Display */}
        <div className="flex-1 overflow-auto p-4 bg-slate-900">
          {error && (
            <div className="bg-red-900/50 border border-red-700 rounded p-4 mb-4">
              <p className="text-red-200">{error}</p>
            </div>
          )}

          {!health?.connected && (
            <div className="flex items-center justify-center h-full">
              <div className="text-center">
                <Camera className="w-16 h-16 mx-auto text-slate-600 mb-4" />
                <p className="text-slate-400">Image capture service not connected</p>
                <p className="text-sm text-slate-500 mt-2">
                  Make sure the image-capture service is running
                </p>
              </div>
            </div>
          )}

          {health?.connected && !imageUrl && (
            <div className="flex items-center justify-center h-full">
              <div className="text-center">
                <Camera className="w-16 h-16 mx-auto text-slate-500 mb-4" />
                <p className="text-slate-400">Click "Capture Image" to get a frame</p>
              </div>
            </div>
          )}

          {imageUrl && (
            <div className="flex items-center justify-center">
              <img
                src={imageUrl}
                alt="Carla Camera View"
                className="max-w-full h-auto rounded border border-slate-700"
                onError={() => setError('Failed to load image')}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default CarlaImageViewer;
