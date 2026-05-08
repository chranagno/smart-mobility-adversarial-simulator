import { Server, Activity, Clock } from 'lucide-react';

function SystemStatus({ services }) {
  const runningServices = services.filter(s => s.status === 'online').length;
  const stoppedServices = services.filter(s => s.status === 'stopped').length;
  const erroredServices = services.filter(s => s.status === 'errored').length;

  const totalCpu = services
    .filter(s => s.status === 'online')
    .reduce((sum, s) => sum + (s.cpu || 0), 0);

  const totalMemory = services
    .filter(s => s.status === 'online')
    .reduce((sum, s) => sum + (s.memory || 0), 0);

  const formatMemory = (bytes) => {
    return (bytes / 1024 / 1024).toFixed(1) + ' MB';
  };

  return (
    <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
      {/* Total Services */}
      <div className="bg-slate-800 rounded-lg p-6 border border-slate-700">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm text-slate-400 mb-1">Total Services</p>
            <p className="text-3xl font-bold text-white">{services.length}</p>
          </div>
          <Server className="w-10 h-10 text-blue-500" />
        </div>
        <div className="mt-4 flex items-center space-x-4 text-sm">
          <span className="text-green-400">{runningServices} running</span>
          <span className="text-red-400">{stoppedServices} stopped</span>
          {erroredServices > 0 && (
            <span className="text-orange-400">{erroredServices} errored</span>
          )}
        </div>
      </div>

      {/* CPU Usage */}
      <div className="bg-slate-800 rounded-lg p-6 border border-slate-700">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm text-slate-400 mb-1">Total CPU</p>
            <p className="text-3xl font-bold text-white">{totalCpu.toFixed(1)}%</p>
          </div>
          <Activity className="w-10 h-10 text-green-500" />
        </div>
        <div className="mt-4">
          <div className="w-full bg-slate-700 rounded-full h-2">
            <div
              className="bg-green-500 h-2 rounded-full transition-all"
              style={{ width: `${Math.min(totalCpu, 100)}%` }}
            ></div>
          </div>
        </div>
      </div>

      {/* Memory Usage */}
      <div className="bg-slate-800 rounded-lg p-6 border border-slate-700">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm text-slate-400 mb-1">Total Memory</p>
            <p className="text-3xl font-bold text-white">{formatMemory(totalMemory)}</p>
          </div>
          <Activity className="w-10 h-10 text-purple-500" />
        </div>
        <p className="mt-4 text-sm text-slate-400">
          Across {runningServices} running service{runningServices !== 1 ? 's' : ''}
        </p>
      </div>

      {/* Status */}
      <div className="bg-slate-800 rounded-lg p-6 border border-slate-700">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm text-slate-400 mb-1">System Status</p>
            <p className="text-3xl font-bold text-white">
              {runningServices > 0 ? 'Active' : 'Idle'}
            </p>
          </div>
          <Clock className="w-10 h-10 text-yellow-500" />
        </div>
        <p className="mt-4 text-sm text-slate-400">
          {runningServices > 0
            ? `${runningServices} service${runningServices !== 1 ? 's' : ''} active`
            : 'No services running'}
        </p>
      </div>
    </div>
  );
}

export default SystemStatus;
