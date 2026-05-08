import { useState, useEffect, useRef } from 'react';
import { X, RefreshCw } from 'lucide-react';
import { api } from '../api';

function LogViewer({ service, onClose }) {
  const [logs, setLogs] = useState('');
  const [loading, setLoading] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const logsEndRef = useRef(null);

  useEffect(() => {
    fetchLogs();
    const interval = setInterval(fetchLogs, 3000); // Refresh logs every 3 seconds
    return () => clearInterval(interval);
  }, [service.name]);

  useEffect(() => {
    if (autoScroll && logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs, autoScroll]);

  const fetchLogs = async () => {
    try {
      setLoading(true);
      const response = await api.getServiceLogs(service.name, 200);
      if (response.success) {
        setLogs(response.logs || 'No logs available');
      } else {
        setLogs(`Error fetching logs: ${response.error}`);
      }
    } catch (err) {
      setLogs(`Error: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-slate-800 rounded-lg w-full max-w-4xl max-h-[80vh] flex flex-col border border-slate-700">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-slate-700">
          <h3 className="text-lg font-semibold text-white">
            Logs: {service.name}
          </h3>
          <div className="flex items-center space-x-2">
            <label className="flex items-center space-x-2 text-sm text-slate-400">
              <input
                type="checkbox"
                checked={autoScroll}
                onChange={(e) => setAutoScroll(e.target.checked)}
                className="rounded"
              />
              <span>Auto-scroll</span>
            </label>
            <button
              onClick={fetchLogs}
              disabled={loading}
              className="p-2 hover:bg-slate-700 rounded transition-colors"
              title="Refresh logs"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button
              onClick={onClose}
              className="p-2 hover:bg-slate-700 rounded transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Logs Content */}
        <div className="flex-1 overflow-y-auto p-4 bg-slate-900">
          <pre className="text-xs text-slate-300 font-mono whitespace-pre-wrap break-words">
            {logs}
          </pre>
          <div ref={logsEndRef} />
        </div>
      </div>
    </div>
  );
}

export default LogViewer;
