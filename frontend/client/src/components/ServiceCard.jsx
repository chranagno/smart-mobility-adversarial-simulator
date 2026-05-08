import { useState } from 'react';
import { Play, Square, RotateCw, FileText, Cpu, MemoryStick } from 'lucide-react';
import { api } from '../api';

function ServiceCard({ service, onViewLogs, onRefresh }) {
  const [isStarting, setIsStarting] = useState(false);
  const [isStopping, setIsStopping] = useState(false);
  const [isRestarting, setIsRestarting] = useState(false);

  const getStatusColor = (status) => {
    switch (status) {
      case 'online':
        return 'bg-green-500';
      case 'stopping':
      case 'stopped':
        return 'bg-red-500';
      case 'launching':
        return 'bg-yellow-500';
      case 'errored':
        return 'bg-orange-500';
      default:
        return 'bg-gray-500';
    }
  };

  const getStatusText = (status) => {
    switch (status) {
      case 'online':
        return 'Running';
      case 'stopped':
        return 'Stopped';
      case 'stopping':
        return 'Stopping';
      case 'launching':
        return 'Starting';
      case 'errored':
        return 'Error';
      default:
        return status;
    }
  };

  const formatMemory = (bytes) => {
    return (bytes / 1024 / 1024).toFixed(1) + ' MB';
  };

  const formatUptime = (timestamp) => {
    if (!timestamp) return 'N/A';
    const uptime = Date.now() - timestamp;
    const seconds = Math.floor(uptime / 1000);
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);
    const days = Math.floor(hours / 24);

    if (days > 0) return `${days}d ${hours % 24}h`;
    if (hours > 0) return `${hours}h ${minutes % 60}m`;
    if (minutes > 0) return `${minutes}m ${seconds % 60}s`;
    return `${seconds}s`;
  };

  const handleStart = async () => {
    setIsStarting(true);
    try {
      await api.startService(service.name);
      setTimeout(onRefresh, 1000);
    } catch (err) {
      console.error('Error starting service:', err);
    } finally {
      setIsStarting(false);
    }
  };

  const handleStop = async () => {
    setIsStopping(true);
    try {
      await api.stopService(service.name);
      setTimeout(onRefresh, 1000);
    } catch (err) {
      console.error('Error stopping service:', err);
    } finally {
      setIsStopping(false);
    }
  };

  const handleRestart = async () => {
    setIsRestarting(true);
    try {
      await api.restartService(service.name);
      setTimeout(onRefresh, 1000);
    } catch (err) {
      console.error('Error restarting service:', err);
    } finally {
      setIsRestarting(false);
    }
  };

  const isRunning = service.status === 'online';

  return (
    <div className="bg-slate-800 rounded-lg p-6 border border-slate-700 hover:border-slate-600 transition-colors">
      {/* Header */}
      <div className="flex items-start justify-between mb-4">
        <div className="flex-1">
          <div className="flex items-center space-x-2 mb-2">
            <div className={`w-3 h-3 rounded-full ${getStatusColor(service.status)}`}></div>
            <h3 className="text-lg font-semibold text-white">{service.name}</h3>
          </div>
          <p className="text-sm text-slate-400">{getStatusText(service.status)}</p>
        </div>
      </div>

      {/* Metrics */}
      {isRunning && (
        <div className="space-y-2 mb-4">
          <div className="flex items-center justify-between text-sm">
            <div className="flex items-center space-x-2 text-slate-400">
              <Cpu className="w-4 h-4" />
              <span>CPU</span>
            </div>
            <span className="text-white font-medium">{service.cpu}%</span>
          </div>
          <div className="flex items-center justify-between text-sm">
            <div className="flex items-center space-x-2 text-slate-400">
              <MemoryStick className="w-4 h-4" />
              <span>Memory</span>
            </div>
            <span className="text-white font-medium">{formatMemory(service.memory)}</span>
          </div>
          <div className="flex items-center justify-between text-sm">
            <span className="text-slate-400">Uptime</span>
            <span className="text-white font-medium">{formatUptime(service.uptime)}</span>
          </div>
          <div className="flex items-center justify-between text-sm">
            <span className="text-slate-400">Restarts</span>
            <span className="text-white font-medium">{service.restarts}</span>
          </div>
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center space-x-2">
        {isRunning ? (
          <>
            <button
              onClick={handleStop}
              disabled={isStopping}
              className="flex-1 flex items-center justify-center space-x-1 px-3 py-2 bg-red-600 hover:bg-red-700 rounded transition-colors disabled:opacity-50 text-sm"
            >
              <Square className="w-4 h-4" />
              <span>{isStopping ? 'Stopping...' : 'Stop'}</span>
            </button>
            <button
              onClick={handleRestart}
              disabled={isRestarting}
              className="flex-1 flex items-center justify-center space-x-1 px-3 py-2 bg-yellow-600 hover:bg-yellow-700 rounded transition-colors disabled:opacity-50 text-sm"
            >
              <RotateCw className="w-4 h-4" />
              <span>{isRestarting ? 'Restarting...' : 'Restart'}</span>
            </button>
          </>
        ) : (
          <button
            onClick={handleStart}
            disabled={isStarting}
            className="flex-1 flex items-center justify-center space-x-1 px-3 py-2 bg-green-600 hover:bg-green-700 rounded transition-colors disabled:opacity-50 text-sm"
          >
            <Play className="w-4 h-4" />
            <span>{isStarting ? 'Starting...' : 'Start'}</span>
          </button>
        )}
        <button
          onClick={onViewLogs}
          className="px-3 py-2 bg-slate-700 hover:bg-slate-600 rounded transition-colors text-sm"
          title="View Logs"
        >
          <FileText className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}

export default ServiceCard;
