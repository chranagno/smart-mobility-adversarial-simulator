import { useEffect, useRef, useState } from 'react';
import { MapPin, Gauge, Navigation } from 'lucide-react';
import { api } from '../api';

const VehicleMap = ({ wsUrl, useGps = false }) => {
  const canvasRef = useRef(null);
  const [vehicles, setVehicles] = useState({});
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState(null);
  const [selectedVehicle, setSelectedVehicle] = useState(null);
  const [cameraVehicle, setCameraVehicle] = useState(null);
  const [cameraError, setCameraError] = useState(null);
  const [cameraStreamUrl, setCameraStreamUrl] = useState(null);
  const [roadNetwork, setRoadNetwork] = useState(null);
  const [showRoads, setShowRoads] = useState(true);
  const [currentTown, setCurrentTown] = useState('Town01');
  const wsRef = useRef(null);
  const previousHeadingsRef = useRef({}); // Store previous headings for smoothing

  // Connect to WebSocket
  useEffect(() => {
    const connectWs = () => {
      try {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        // Construct WebSocket URL - prefer explicit prop or env, then fall back intelligently
        let url = wsUrl || import.meta.env.VITE_VEHICLE_WS_URL;
        if (!url) {
          const isLocalDev = ['localhost', '127.0.0.1'].includes(window.location.hostname) &&
            (window.location.port === '5173' || window.location.port === '4173');
          const host = isLocalDev
            ? `${window.location.hostname}:3001` // dev server needs to hit backend port
            : window.location.host;
          url = `${protocol}//${host}/vehicles`;
        } else if (url.startsWith('/')) {
          // Relative URL - construct from current host
          url = `${protocol}//${window.location.host}${url}`;
        }
        
        console.log('[VehicleMap] Connecting to WebSocket:', url);
        console.log('[VehicleMap] Current window location:', window.location.href);
        const ws = new WebSocket(url);
        wsRef.current = ws;

        ws.onopen = () => {
          console.log('[VehicleMap] ✅ Successfully connected to vehicle position stream');
          console.log('[VehicleMap] WebSocket readyState:', ws.readyState);
          setConnected(true);
          setError(null);
        };

        ws.onmessage = async (event) => {
          try {
            // Handle both string and Blob data
            let textData;
            if (event.data instanceof Blob) {
              textData = await event.data.text();
            } else {
              textData = event.data;
            }

            const data = JSON.parse(textData);
            console.log('[VehicleMap] Received message:', data.type, 'vehicles:', data.vehicles?.length);
            console.log('[VehicleMap] Full message data:', JSON.stringify(data, null, 2));
            
            // Handle error messages from server
            if (data.type === 'error') {
                console.error('[VehicleMap] Server error:', data.message);
                setError(data.message);
                // Don't set connected to false - we're still connected to Node.js proxy
                // The error is about Python server not being available
                return;
            }
            
            if (data.type === 'vehicle_positions' && data.vehicles) {
              console.log('[VehicleMap] Processing', data.vehicles.length, 'vehicles');
              // Update vehicles state with smoothed headings
              const vehiclesMap = {};
              data.vehicles.forEach((vehicle, idx) => {
                // Debug first vehicle
                if (idx === 0) {
                  console.log('[VehicleMap] Sample vehicle structure:', vehicle);
                  console.log('[VehicleMap] Vehicle keys:', Object.keys(vehicle));
                  console.log('[VehicleMap] carla_x:', vehicle.carla_x, 'carla_y:', vehicle.carla_y);
                  console.log('[VehicleMap] gps_lon:', vehicle.gps_lon, 'gps_lat:', vehicle.gps_lat);
                }
              });
              data.vehicles.forEach(vehicle => {
                // Smooth heading to prevent jitter from small changes
                // if (vehicle.heading !== undefined && vehicle.heading !== null) {
                //   const prevHeading = previousHeadingsRef.current[vehicle.id];
                //   if (prevHeading !== undefined) {
                //     // Normalize angles to 0-360 range
                //     let currentHeading = vehicle.heading % 360;
                //     if (currentHeading < 0) currentHeading += 360;
                //     let prevHeadingNorm = prevHeading % 360;
                //     if (prevHeadingNorm < 0) prevHeadingNorm += 360;

                //     // Calculate shortest angular distance
                //     let diff = currentHeading - prevHeadingNorm;
                //     if (diff > 180) diff -= 360;
                //     if (diff < -180) diff += 360;

                //     // Only update if change is significant (> 0.5 degrees) to reduce jitter
                //     if (Math.abs(diff) > 0.5) {
                //       vehicle.heading = prevHeadingNorm + diff;
                //       previousHeadingsRef.current[vehicle.id] = vehicle.heading;
                //     } else {
                //       // Use previous heading if change is too small
                //       vehicle.heading = prevHeadingNorm;
                //     }
                //   } else {
                //     // First time seeing this vehicle, normalize and store
                //     let normalizedHeading = vehicle.heading % 360;
                //     if (normalizedHeading < 0) normalizedHeading += 360;
                //     vehicle.heading = normalizedHeading;
                //     previousHeadingsRef.current[vehicle.id] = normalizedHeading;
                //   }
                // }
                vehiclesMap[vehicle.id] = vehicle;
              });
              console.log('[VehicleMap] Updating state with', Object.keys(vehiclesMap).length, 'vehicles');
              setVehicles(vehiclesMap);
            }
          } catch (err) {
            console.error('[VehicleMap] Error parsing message:', err);
          }
        };

        ws.onerror = (err) => {
          console.error('[VehicleMap] WebSocket error:', err);
          console.error('[VehicleMap] Error details:', {
            type: err.type,
            target: err.target,
            currentTarget: err.currentTarget,
            url: err.target?.url
          });
          const errorMsg = err.message || err.target?.url 
            ? `Unable to connect to ${err.target.url}. Check if the server is running on port 3001.`
            : 'Unable to connect to vehicle stream. Make sure the server is running.';
          setError(errorMsg);
          setConnected(false);
        };

        ws.onclose = (event) => {
          console.log('[VehicleMap] Disconnected from vehicle position stream', event.code, event.reason);
          setConnected(false);
          if (event.code !== 1000) { // Not a normal closure
            setError(`Connection closed unexpectedly (code: ${event.code}). Attempting to reconnect...`);
          }
          // Attempt to reconnect after 3 seconds
          setTimeout(connectWs, 3000);
        };
      } catch (err) {
        console.error('[VehicleMap] Failed to connect:', err);
        setError(err.message);
        setTimeout(connectWs, 3000);
      }
    };

    connectWs();

    return () => {
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, [wsUrl]);

  // Load current scenario to get town name
  useEffect(() => {
    const loadCurrentTown = async () => {
      try {
        const response = await api.getCurrentScenario();
        if (response.success && response.scenario) {
          // Extract town from scenario config
          const town = response.scenario.town || response.scenario.carla?.town || 
                      response.scenario.world?.town || 'Town01';
          setCurrentTown(town);
        } else {
          // Try to get town from simulation status
          const statusResponse = await api.simulation.getStatus();
          if (statusResponse.success && statusResponse.scenario) {
            const town = statusResponse.scenario.town || 'Town01';
            setCurrentTown(town);
          }
        }
      } catch (err) {
        console.warn('[VehicleMap] Could not load current scenario, using default Town01:', err);
      }
    };
    
    loadCurrentTown();
    // Refresh town every 10 seconds in case scenario changes
    const interval = setInterval(loadCurrentTown, 10000);
    return () => clearInterval(interval);
  }, []);

  // Load road network based on current town
  useEffect(() => {
    const networkPath = `/${currentTown}_network.json`;
    console.log('[VehicleMap] Loading road network from:', networkPath);
    
    fetch(networkPath)
      .then(res => {
        if (!res.ok) {
          throw new Error(`Failed to load road network: ${res.status} ${res.statusText}`);
        }
        return res.json();
      })
      .then(data => {
        console.log('[VehicleMap] Loaded road network with', data.features?.length, 'features');
        setRoadNetwork(data);
        setError(null); // Clear any previous errors
      })
      .catch(err => {
        console.error('[VehicleMap] Failed to load road network:', err);
        // Try to fall back to Town01 if the specific town network is not found
        if (currentTown !== 'Town01') {
          console.log('[VehicleMap] Falling back to Town01 network');
          fetch('/Town01_network.json')
            .then(res => res.json())
            .then(data => {
              console.log('[VehicleMap] Loaded fallback Town01 network');
              setRoadNetwork(data);
            })
            .catch(fallbackErr => {
              console.error('[VehicleMap] Failed to load fallback network:', fallbackErr);
              setError(`Road network not available for ${currentTown}`);
            });
        } else {
          setError('Road network not available');
        }
      });
  }, [currentTown]);

  // Generate consistent color from vehicle ID
  const getColorFromId = (id) => {
    // Hash the ID to get a consistent number
    let hash = 0;
    const idStr = String(id);
    for (let i = 0; i < idStr.length; i++) {
      hash = idStr.charCodeAt(i) + ((hash << 5) - hash);
    }
    
    // Use hash to select from color palette
    const colors = [
      '#ef4444', // red
      '#3b82f6', // blue
      '#10b981', // green
      '#f59e0b', // amber
      '#8b5cf6', // violet
      '#ec4899', // pink
      '#06b6d4', // cyan
      '#f97316', // orange
      '#84cc16', // lime
      '#14b8a6', // teal
      '#a855f7', // purple
      '#f43f5e', // rose
    ];
    
    // Use absolute value of hash to get consistent color
    return colors[Math.abs(hash) % colors.length];
  };

  // Draw vehicles on canvas
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    const width = canvas.width;
    const height = canvas.height;

    // Clear canvas
    ctx.fillStyle = '#1e293b'; // slate-800
    ctx.fillRect(0, 0, width, height);

    const vehicleList = Object.values(vehicles);
    console.log('[VehicleMap] Drawing', vehicleList.length, 'vehicles');
    
    // Debug: log vehicle data
    if (vehicleList.length > 0) {
      console.log('[VehicleMap] Sample vehicle data:', vehicleList[0]);
      vehicleList.forEach((vehicle, idx) => {
        const x = useGps ? vehicle.gps_lon : vehicle.carla_x;
        const y = useGps ? vehicle.gps_lat : vehicle.carla_y;
        console.log(`[VehicleMap] Vehicle ${idx} (${vehicle.id}): x=${x}, y=${y}, useGps=${useGps}`);
      });
    }

    // Calculate bounds from both vehicles and road network
    let minX = Infinity, maxX = -Infinity;
    let minY = Infinity, maxY = -Infinity;

    // Include vehicle positions in bounds
    let validVehicles = 0;
    vehicleList.forEach(vehicle => {
      const x = useGps ? vehicle.gps_lon : vehicle.carla_x;
      const y = useGps ? vehicle.gps_lat : vehicle.carla_y;
      if (x !== null && y !== null && x !== undefined && y !== undefined && isFinite(x) && isFinite(y)) {
        minX = Math.min(minX, x);
        maxX = Math.max(maxX, x);
        minY = Math.min(minY, y);
        maxY = Math.max(maxY, y);
        validVehicles++;
      }
    });
    
    console.log('[VehicleMap] Valid vehicles with coordinates:', validVehicles, 'out of', vehicleList.length);

    // Include road network in bounds
    if (showRoads && roadNetwork) {
      roadNetwork.features.forEach(feature => {
        if (feature.geometry.type === 'LineString') {
          feature.geometry.coordinates.forEach(([x, y]) => {
            minX = Math.min(minX, x);
            maxX = Math.max(maxX, x);
            minY = Math.min(minY, y);
            maxY = Math.max(maxY, y);
          });
        } else if (feature.geometry.type === 'Point') {
          const [x, y] = feature.geometry.coordinates;
          minX = Math.min(minX, x);
          maxX = Math.max(maxX, x);
          minY = Math.min(minY, y);
          maxY = Math.max(maxY, y);
        }
      });
    }

    // If no data at all, show message
    if (!isFinite(minX)) {
      ctx.fillStyle = '#64748b'; // slate-500
      ctx.font = '16px sans-serif';
      ctx.textAlign = 'center';
      if (connected && vehicleList.length === 0) {
        ctx.fillText('Connected but no vehicles received yet...', width / 2, height / 2 - 20);
        ctx.font = '12px sans-serif';
        ctx.fillStyle = '#94a3b8'; // slate-400
        ctx.fillText('Make sure the simulation is running and vehicles are spawned', width / 2, height / 2 + 10);
      } else if (!connected) {
        ctx.fillText('Connecting to vehicle stream...', width / 2, height / 2 - 20);
        ctx.font = '12px sans-serif';
        ctx.fillStyle = '#94a3b8'; // slate-400
        ctx.fillText('Waiting for WebSocket connection', width / 2, height / 2 + 10);
      } else {
        ctx.fillText('Waiting for vehicle data...', width / 2, height / 2 - 20);
        ctx.font = '12px sans-serif';
        ctx.fillStyle = '#94a3b8'; // slate-400
        ctx.fillText(`Received ${vehicleList.length} vehicles but no valid coordinates`, width / 2, height / 2 + 10);
      }
      return;
    }

    // Add padding
    const padding = 50;
    const rangeX = maxX - minX || 1;
    const rangeY = maxY - minY || 1;

    // ============================================
    // MIRRORING CONFIGURATION - Easy to experiment
    // ============================================
    const MIRROR_X = true;  // Set to true to mirror horizontally (flip left-right)
    const MIRROR_Y = true;  // Set to true to mirror vertically (flip top-bottom)
    // ============================================

    // Base scaling functions (normalized to 0-1 range, then scaled to canvas)
    const baseScaleX = (x) => ((x - minX) / rangeX) * (width - 2 * padding) + padding;
    const baseScaleY = (y) => ((y - minY) / rangeY) * (height - 2 * padding) + padding;

    // Vehicle scaling (mirror X, but not Y)
    const scaleVehicleX = (x) => {
      const base = baseScaleX(x);
      return width - base; // Mirror X for vehicles
    };

    const scaleVehicleY = (y) => {
      const base = baseScaleY(y);
      // Canvas Y increases downward, so flip for normal display
      return height - base; // No mirroring for vehicles (normal Y)
    };

    // Road network scaling (WITH mirroring based on configuration)
    const scaleRoadX = (x) => {
      const base = baseScaleX(x);
      return MIRROR_X ? width - base : base;
    };

    const scaleRoadY = (y) => {
      const base = baseScaleY(y);
      // Canvas Y increases downward, so flip for normal display
      const normalY = height - base;
      // Apply Y mirroring: if MIRROR_Y is true, flip again (back to base), otherwise keep normal
      return MIRROR_Y ? base : normalY;
    };

    // Draw road network
    if (showRoads && roadNetwork) {
      roadNetwork.features.forEach(feature => {
        if (feature.geometry.type === 'LineString' && feature.properties.type === 'edge') {
          ctx.strokeStyle = '#475569'; // slate-600 for roads
          ctx.lineWidth = 2;
          ctx.beginPath();

          const coords = feature.geometry.coordinates;
          if (coords.length > 0) {
            const [x0, y0] = coords[0];
            ctx.moveTo(scaleRoadX(x0), scaleRoadY(y0));

            for (let i = 1; i < coords.length; i++) {
              const [x, y] = coords[i];
              ctx.lineTo(scaleRoadX(x), scaleRoadY(y));
            }
          }
          ctx.stroke();
        } else if (feature.geometry.type === 'Point' && feature.properties.type === 'junction') {
          // Draw junctions as small circles
          const [x, y] = feature.geometry.coordinates;
          ctx.fillStyle = '#64748b'; // slate-500
          ctx.beginPath();
          ctx.arc(scaleRoadX(x), scaleRoadY(y), 3, 0, 2 * Math.PI);
          ctx.fill();
        }
      });
    }

    // Draw grid
    ctx.strokeStyle = '#334155'; // slate-700
    ctx.lineWidth = 1;
    for (let i = 0; i <= 10; i++) {
      const x = (width / 10) * i;
      const y = (height / 10) * i;

      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, height);
      ctx.stroke();

      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
    }

    // Draw vehicles
    vehicleList.forEach((vehicle) => {
      const x = useGps ? vehicle.gps_lon : vehicle.carla_x;
      const y = useGps ? vehicle.gps_lat : vehicle.carla_y;

      if (x === null || y === null) return;

      const px = scaleVehicleX(x);
      const py = scaleVehicleY(y);
      const color = getColorFromId(vehicle.id);

      // Draw vehicle as circle
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(px, py, 8, 0, 2 * Math.PI);
      ctx.fill();

      // Draw heading indicator
      // if (vehicle.heading !== undefined && vehicle.heading !== null) {
      //   // Convert heading from degrees to radians
      //   // CARLA/SUMO heading: 0° = North, 90° = East, 180° = South, 270° = West
      //   // Canvas: 0° = right (East), 90° = down (South), 180° = left (West), 270° = up (North)
      //   // Convert CARLA heading to canvas angle: canvas_angle = 90 - carla_heading
      //   // Also account for flipped Y-axis: canvas Y increases downward
      //   const headingDeg = vehicle.heading;
      //   const canvasAngle = 90 - headingDeg; // Convert CARLA (0°=North) to canvas (0°=right)
      //   const headingRad = (canvasAngle * Math.PI) / 180;
      //   const lineLength = 15;

      //   // Calculate end point of heading line
      //   const endX = px + Math.cos(headingRad) * lineLength;
      //   const endY = py + Math.sin(headingRad) * lineLength; // Y increases downward in canvas

      //   ctx.strokeStyle = color;
      //   ctx.lineWidth = 2;
      //   ctx.beginPath();
      //   ctx.moveTo(px, py);
      //   ctx.lineTo(endX, endY);
      //   ctx.stroke();

      //   // Draw arrowhead at the end
      //   const arrowLength = 5;
      //   const arrowAngle = Math.PI / 6; // 30 degrees
      //   const angle1 = headingRad + Math.PI - arrowAngle;
      //   const angle2 = headingRad + Math.PI + arrowAngle;

      //   ctx.beginPath();
      //   ctx.moveTo(endX, endY);
      //   ctx.lineTo(endX + Math.cos(angle1) * arrowLength, endY + Math.sin(angle1) * arrowLength);
      //   ctx.moveTo(endX, endY);
      //   ctx.lineTo(endX + Math.cos(angle2) * arrowLength, endY + Math.sin(angle2) * arrowLength);
      //   ctx.stroke();
      // }

      // Draw vehicle ID with background for better visibility
      ctx.font = 'bold 12px sans-serif';
      ctx.textAlign = 'center';
      const idText = vehicle.id;
      const idMetrics = ctx.measureText(idText);
      const idPadding = 4;
      const idWidth = idMetrics.width + idPadding * 2;
      const idHeight = 16;
      
      // Draw background for ID
      ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
      ctx.fillRect(px - idWidth / 2, py - 15 - idHeight, idWidth, idHeight);
      
      // Draw ID text
      ctx.fillStyle = '#ffffff';
      ctx.fillText(idText, px, py - 7);

      // Draw heading value if available
      // if (vehicle.heading !== undefined && vehicle.heading !== null) {
      //   const headingText = `${vehicle.heading.toFixed(0)}°`;
      //   const headingMetrics = ctx.measureText(headingText);
      //   const headingWidth = headingMetrics.width + idPadding * 2;
      //   const headingHeight = 16;
        
      //   // Draw background for heading
      //   ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
      //   ctx.fillRect(px - headingWidth / 2, py + 20, headingWidth, headingHeight);
        
      //   // Draw heading text
      //   ctx.fillStyle = color;
      //   ctx.fillText(headingText, px, py + 32);
      // }

      // Draw border if selected
      if (selectedVehicle === vehicle.id) {
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.arc(px, py, 12, 0, 2 * Math.PI);
        ctx.stroke();
      }
    });

    // Draw legend
    const legendHeight = showRoads && roadNetwork ? 80 : 60;
    ctx.fillStyle = '#1e293b';
    ctx.fillRect(10, 10, 200, legendHeight);
    ctx.strokeStyle = '#475569';
    ctx.lineWidth = 2;
    ctx.strokeRect(10, 10, 200, legendHeight);

    ctx.fillStyle = '#e2e8f0';
    ctx.font = 'bold 14px sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText(`Vehicles: ${vehicleList.length}`, 20, 30);
    ctx.fillText(`Mode: ${useGps ? 'GPS' : 'Carla'}`, 20, 50);
    // if (showRoads && roadNetwork) {
    //   const edges = roadNetwork.features.filter(f => f.properties.type === 'edge').length;
    //   ctx.fillText(`Roads: ${edges}`, 20, 70);
    // }

  }, [vehicles, useGps, selectedVehicle, roadNetwork, showRoads]);

  const vehicleList = Object.values(vehicles);

  return (
    <div className="bg-slate-800 rounded-lg shadow-xl p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <MapPin className="w-5 h-5 text-blue-400" />
          <h3 className="text-lg font-semibold text-white">Vehicle Map</h3>
        </div>
        <div className="flex items-center gap-4">
          <button
            onClick={() => setShowRoads(!showRoads)}
            className={`px-3 py-1 rounded text-xs font-medium transition-colors ${showRoads
                ? 'bg-blue-600 text-white hover:bg-blue-700'
                : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
              }`}
          >
            {showRoads ? 'Hide Roads' : 'Show Roads'}
          </button>
          <div className="flex items-center gap-2">
            <div className={`w-2 h-2 rounded-full ${connected ? 'bg-green-500 animate-pulse' : 'bg-red-500'}`}></div>
            <span className="text-sm text-slate-400">
              {connected 
                ? (error ? 'Connected (Python server unavailable)' : 'Connected')
                : (error ? 'Error' : 'Connecting...')}
            </span>
          </div>
        </div>
      </div>

      {/* Error message */}
      {error && (
        <div className="bg-red-900/50 border border-red-500 text-red-200 px-4 py-2 rounded mb-4">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Canvas */}
        <div className="lg:col-span-2">
          <canvas
            ref={canvasRef}
            width={800}
            height={600}
            className="w-full border-2 border-slate-700 rounded-lg"
            style={{ maxWidth: '100%', height: 'auto' }}
          />
        </div>

        {/* Vehicle List */}
        <div className="bg-slate-900 rounded-lg p-4 max-h-[600px] overflow-y-auto">
          <h4 className="text-md font-semibold text-white mb-3">Vehicles</h4>
          {vehicleList.length === 0 ? (
            <p className="text-slate-500 text-sm">No vehicles detected</p>
          ) : (
            <div className="space-y-2">
              {vehicleList.map((vehicle) => {
                // Map color hex to Tailwind class
                const colorHex = getColorFromId(vehicle.id);
                const colorMap = {
                  '#ef4444': 'border-red-500',
                  '#3b82f6': 'border-blue-500',
                  '#10b981': 'border-green-500',
                  '#f59e0b': 'border-amber-500',
                  '#8b5cf6': 'border-violet-500',
                  '#ec4899': 'border-pink-500',
                  '#06b6d4': 'border-cyan-500',
                  '#f97316': 'border-orange-500',
                  '#84cc16': 'border-lime-500',
                  '#14b8a6': 'border-teal-500',
                  '#a855f7': 'border-purple-500',
                      '#f43f5e': 'border-rose-500',
                    };
                    const borderColor = colorMap[colorHex] || 'border-slate-500';

                    return (
                      <div
                        key={vehicle.id}
                        onClick={() => {
                          setSelectedVehicle(vehicle.id === selectedVehicle ? null : vehicle.id);
                        }}
                        className={`p-3 bg-slate-800 border-l-4 ${borderColor} rounded cursor-pointer hover:bg-slate-700 transition-colors ${selectedVehicle === vehicle.id ? 'ring-2 ring-white' : ''
                          }`}
                      >
                        <div className="text-sm font-semibold text-white mb-1">{vehicle.id}</div>
                    <div className="text-xs text-slate-400 space-y-1">
                      {/* Display CARLA ID for debugging - show carla_id if available, otherwise show id as CARLA ID */}
                      <div className="text-[11px] text-blue-400 font-mono">
                        CARLA ID: {vehicle.carla_id !== undefined ? vehicle.carla_id : vehicle.id}
                      </div>
                      {vehicle.role_name && (
                        <div className="text-[11px] text-slate-500">role: {vehicle.role_name}</div>
                      )}
                      {useGps ? (
                        <div className="flex items-center gap-1">
                          <MapPin className="w-3 h-3" />
                          <span>
                            {vehicle.gps_lat?.toFixed(6)}, {vehicle.gps_lon?.toFixed(6)}
                          </span>
                        </div>
                      ) : (
                        <div className="flex items-center gap-1">
                          <MapPin className="w-3 h-3" />
                          <span>
                            ({vehicle.carla_x?.toFixed(2)}, {vehicle.carla_y?.toFixed(2)})
                          </span>
                        </div>
                      )}
                      <div className="flex items-center gap-1">
                        <Gauge className="w-3 h-3" />
                        <span>{vehicle.speed?.toFixed(2)} m/s</span>
                      </div>
                      <div className="flex items-center gap-1">
                        <Navigation className="w-3 h-3" />
                        <span>{vehicle.heading?.toFixed(2)}°</span>
                      </div>
                    </div>
                    <div className="mt-2 flex justify-end">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelectedVehicle(vehicle.id);
                          const cameraId = vehicle.carla_id !== undefined ? vehicle.carla_id : vehicle.id; // Prefer Carla actor ID when available
                          setCameraVehicle(cameraId);
                          // Update stream URL with cache-busting timestamp
                          setCameraStreamUrl(`/api/carla/stream/vehicle/${cameraId}?width=960&height=540&fps=10&t=${Date.now()}`);
                          setCameraError(null);
                        }}
                        className="px-3 py-1 bg-blue-600 hover:bg-blue-700 text-white text-xs rounded transition-colors"
                      >
                        View Camera
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Camera view for selected vehicle */}
      {cameraVehicle && (
        <div className="mt-4 bg-slate-900 border border-slate-700 rounded-lg p-4">
          <div className="flex items-center justify-between mb-2">
            <div className="text-sm text-white font-semibold">
              Live camera — Vehicle {cameraVehicle}
            </div>
            <button
              onClick={() => {
                setCameraVehicle(null);
                setCameraError(null);
                setCameraStreamUrl(null);
              }}
              className="text-xs px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded text-white"
            >
              Close
            </button>
          </div>
          {cameraError && (
            <div className="bg-red-900/50 border border-red-700 rounded p-3 mb-2">
              <p className="text-red-200 text-sm">{cameraError}</p>
              <p className="text-red-300 text-xs mt-1">
                Make sure the image-capture service is running and the vehicle exists.
              </p>
            </div>
          )}
          <div className="w-full bg-black rounded overflow-hidden border border-slate-800">
            {cameraStreamUrl ? (
              <img
                key={cameraVehicle} // Force re-render when vehicle changes
                src={cameraStreamUrl}
                alt={`Vehicle ${cameraVehicle} camera stream`}
                className="w-full h-auto"
                onError={(e) => {
                  console.error('[VehicleMap] Failed to load camera stream for vehicle', cameraVehicle);
                  setCameraError(`Failed to load camera stream for vehicle ${cameraVehicle}. The vehicle may not exist or the image-capture service may not be running.`);
                }}
                onLoad={() => {
                  setCameraError(null); // Clear error if image loads successfully
                }}
              />
            ) : (
              <div className="w-full h-64 flex items-center justify-center text-slate-500">
                <div className="text-center">
                  <p>Loading camera stream...</p>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default VehicleMap;
