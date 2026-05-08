import { useState, useEffect, useRef } from 'react';
import { Camera, Activity, Radio, MapPin, Gauge, ArrowUp, RotateCcw } from 'lucide-react';

/**
 * EgoSensorMonitor — live ego vehicle camera feed, telemetry, and V2X status.
 *
 * Receives `ego_sensor_data` messages from the orchestrator WebSocket:
 * {
 *   type: 'ego_sensor_data',
 *   telemetry: { speed_kmh, speed_ms, x, y, z, yaw, acceleration, … },
 *   camera: { sensor_id, jpeg_base64 },
 *   recording: bool,
 *   timestamp: float
 * }
 */
const EgoSensorMonitor = ({ wsUrl }) => {
  const [telemetry, setTelemetry] = useState(null);
  const [cameraFrame, setCameraFrame] = useState(null);
  const [isRecording, setIsRecording] = useState(false);
  const [v2xCount, setV2xCount] = useState(0);
  const [connected, setConnected] = useState(false);
  const [fps, setFps] = useState(0);
  const wsRef = useRef(null);
  const frameCountRef = useRef(0);
  const fpsIntervalRef = useRef(null);

  // FPS counter
  useEffect(() => {
    fpsIntervalRef.current = setInterval(() => {
      setFps(frameCountRef.current);
      frameCountRef.current = 0;
    }, 1000);
    return () => clearInterval(fpsIntervalRef.current);
  }, []);

  // WebSocket connection — connects to orchestrator WS (same as VehicleMap)
  useEffect(() => {
    let ws;
    let reconnectTimer;

    const connect = () => {
      // Build WS URL: prefer prop, then env, then auto-detect
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      let url = wsUrl || import.meta.env.VITE_VEHICLE_WS_URL;
      if (!url) {
        const isLocalDev = ['localhost', '127.0.0.1'].includes(window.location.hostname) &&
          (window.location.port === '5173' || window.location.port === '4173');
        const host = isLocalDev
          ? `${window.location.hostname}:3001`
          : window.location.host;
        url = `${protocol}//${host}/vehicles`;
      } else if (url.startsWith('/')) {
        url = `${protocol}//${window.location.host}${url}`;
      }

      ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        console.log('[EgoSensorMonitor] Connected to WebSocket');
        setConnected(true);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'ego_sensor_data') {
            frameCountRef.current += 1;

            if (data.telemetry) {
              setTelemetry(data.telemetry);
            }
            if (data.camera?.jpeg_base64) {
              setCameraFrame(`data:image/jpeg;base64,${data.camera.jpeg_base64}`);
            }
            if (data.recording !== undefined) {
              setIsRecording(data.recording);
            }
          }
          // Count V2X messages from vehicle_positions (CAMs are embedded)
          if (data.type === 'vehicle_positions') {
            setV2xCount(prev => prev + 1);
          }
        } catch (e) {
          // ignore parse errors
        }
      };

      ws.onclose = () => {
        setConnected(false);
        // Auto-reconnect after 3 seconds
        reconnectTimer = setTimeout(connect, 3000);
      };

      ws.onerror = () => {
        setConnected(false);
      };
    };

    connect();

    return () => {
      clearTimeout(reconnectTimer);
      if (ws) ws.close();
    };
  }, [wsUrl]);

  const formatNumber = (val, decimals = 1) => {
    if (val === null || val === undefined) return '—';
    return Number(val).toFixed(decimals);
  };

  return (
    <div className="bg-gradient-to-br from-slate-800 to-slate-900 rounded-xl shadow-2xl border border-slate-700 overflow-hidden">
      {/* Header */}
      <div className="bg-slate-900/50 px-5 py-3 border-b border-slate-700 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 bg-gradient-to-br from-cyan-500 to-blue-600 rounded-lg flex items-center justify-center shadow-lg">
            <Camera className="w-5 h-5 text-white" />
          </div>
          <div>
            <h3 className="text-lg font-bold text-white">Ego Sensor Monitor</h3>
            <p className="text-xs text-slate-400">
              {telemetry?.sumo_id ? `Vehicle: ${telemetry.sumo_id}` : 'Waiting for ego…'}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Recording badge */}
          {isRecording && (
            <div className="flex items-center gap-1.5 px-3 py-1 bg-red-900/40 border border-red-700/50 rounded-full">
              <div className="w-2 h-2 bg-red-500 rounded-full animate-pulse" />
              <span className="text-xs font-medium text-red-400">REC</span>
            </div>
          )}

          {/* Connection indicator */}
          <div className={`w-2.5 h-2.5 rounded-full ${connected ? 'bg-green-500' : 'bg-slate-500'}`} />
          <span className="text-xs text-slate-400">{fps} fps</span>
        </div>
      </div>

      <div className="p-4 space-y-4">
        {/* Camera feed */}
        <div className="relative rounded-lg overflow-hidden bg-black aspect-video">
          {cameraFrame ? (
            <img
              src={cameraFrame}
              alt="Ego camera"
              className="w-full h-full object-cover"
            />
          ) : (
            <div className="flex items-center justify-center h-full text-slate-500">
              <div className="text-center">
                <Camera className="w-12 h-12 mx-auto mb-2 opacity-30" />
                <p className="text-sm">No camera feed</p>
                <p className="text-xs text-slate-600 mt-1">
                  Waiting for ego vehicle sensors…
                </p>
              </div>
            </div>
          )}

          {/* Overlay: Speed */}
          {telemetry && (
            <div className="absolute bottom-3 left-3 bg-black/60 backdrop-blur-sm rounded-lg px-3 py-2">
              <span className="text-2xl font-bold font-mono text-white">
                {formatNumber(telemetry.speed_kmh, 0)}
              </span>
              <span className="text-xs text-slate-300 ml-1">km/h</span>
            </div>
          )}
        </div>

        {/* Telemetry grid */}
        {telemetry && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {/* Position */}
            <div className="bg-slate-800/60 rounded-lg p-3 border border-slate-700/50">
              <div className="flex items-center gap-1.5 mb-1.5">
                <MapPin className="w-3.5 h-3.5 text-cyan-400" />
                <span className="text-xs text-slate-400">Position</span>
              </div>
              <div className="space-y-0.5 text-xs font-mono text-slate-300">
                <div>X: {formatNumber(telemetry.x)}</div>
                <div>Y: {formatNumber(telemetry.y)}</div>
                <div>Z: {formatNumber(telemetry.z)}</div>
              </div>
            </div>

            {/* Speed */}
            <div className="bg-slate-800/60 rounded-lg p-3 border border-slate-700/50">
              <div className="flex items-center gap-1.5 mb-1.5">
                <Gauge className="w-3.5 h-3.5 text-green-400" />
                <span className="text-xs text-slate-400">Speed</span>
              </div>
              <div className="text-xl font-bold font-mono text-white">
                {formatNumber(telemetry.speed_kmh, 0)}
                <span className="text-xs text-slate-400 ml-1">km/h</span>
              </div>
              <div className="text-xs font-mono text-slate-500">
                {formatNumber(telemetry.speed_ms)} m/s
              </div>
            </div>

            {/* Acceleration */}
            <div className="bg-slate-800/60 rounded-lg p-3 border border-slate-700/50">
              <div className="flex items-center gap-1.5 mb-1.5">
                <ArrowUp className="w-3.5 h-3.5 text-amber-400" />
                <span className="text-xs text-slate-400">Acceleration</span>
              </div>
              <div className="space-y-0.5 text-xs font-mono text-slate-300">
                <div>X: {formatNumber(telemetry.acceleration?.x)}</div>
                <div>Y: {formatNumber(telemetry.acceleration?.y)}</div>
                <div>Z: {formatNumber(telemetry.acceleration?.z)}</div>
              </div>
            </div>

            {/* Heading & V2X */}
            <div className="bg-slate-800/60 rounded-lg p-3 border border-slate-700/50">
              <div className="flex items-center gap-1.5 mb-1.5">
                <RotateCcw className="w-3.5 h-3.5 text-purple-400" />
                <span className="text-xs text-slate-400">Heading</span>
              </div>
              <div className="text-xl font-bold font-mono text-white">
                {formatNumber(telemetry.yaw, 0)}°
              </div>
              <div className="flex items-center gap-1.5 mt-2 pt-2 border-t border-slate-700/50">
                <Radio className="w-3 h-3 text-blue-400" />
                <span className="text-xs text-slate-400">V2X: {v2xCount} msgs</span>
              </div>
            </div>
          </div>
        )}

        {/* No telemetry placeholder */}
        {!telemetry && (
          <div className="flex items-center justify-center py-6 text-slate-500">
            <Activity className="w-5 h-5 mr-2 animate-pulse" />
            <span className="text-sm">Waiting for telemetry data…</span>
          </div>
        )}
      </div>
    </div>
  );
};

export default EgoSensorMonitor;
