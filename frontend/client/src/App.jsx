import { useState, useEffect } from 'react'
import Dashboard from './components/Dashboard'
import { api, connectWebSocket } from './api'

function App() {
  const [services, setServices] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Fetch services on mount
  useEffect(() => {
    fetchServices();
  }, []);

  // Connect to WebSocket for real-time updates
  useEffect(() => {
    const ws = connectWebSocket((data) => {
      if (data.type === 'status_update') {
        setServices(prevServices => {
          const prevByName = new Map(prevServices.map(s => [s.name, s]));
          const updateByName = new Map(data.services.map(s => [s.name, s]));
          // Update existing, add new, drop removed
          const merged = [];
          updateByName.forEach((update, name) => {
            const existing = prevByName.get(name);
            merged.push(existing ? { ...existing, ...update } : update);
          });
          return merged;
        });
      } else if (data.type === 'service_started' ||
                 data.type === 'service_stopped' ||
                 data.type === 'service_restarted' ||
                 data.type === 'all_services_started' ||
                 data.type === 'all_services_stopped') {
        fetchServices();
      }
    });

    return () => ws.close();
  }, []);

  const fetchServices = async () => {
    try {
      setLoading(true);
      const response = await api.getServices();
      if (response.success) {
        setServices(response.services);
        setError(null);
      } else {
        setError(response.error);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-900 text-white">
      <Dashboard
        services={services}
        loading={loading}
        error={error}
        onRefresh={fetchServices}
      />
    </div>
  )
}

export default App
