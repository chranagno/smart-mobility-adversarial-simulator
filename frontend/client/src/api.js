const API_BASE = '/api';

export const api = {
  // Get all services
  getServices: async () => {
    const response = await fetch(`${API_BASE}/services`);
    return response.json();
  },

  // Start a service
  startService: async (name) => {
    const response = await fetch(`${API_BASE}/services/${name}/start`, {
      method: 'POST',
    });
    return response.json();
  },

  // Stop a service
  stopService: async (name) => {
    const response = await fetch(`${API_BASE}/services/${name}/stop`, {
      method: 'POST',
    });
    return response.json();
  },

  // Restart a service
  restartService: async (name) => {
    const response = await fetch(`${API_BASE}/services/${name}/restart`, {
      method: 'POST',
    });
    return response.json();
  },

  // Get service logs
  getServiceLogs: async (name, lines = 100) => {
    const response = await fetch(`${API_BASE}/services/${name}/logs?lines=${lines}`);
    return response.json();
  },

  // Start all services
  startAll: async (scenario = null) => {
    const response = await fetch(`${API_BASE}/services/start-all`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ scenario }),
    });
    return response.json();
  },

  // Stop all services
  stopAll: async () => {
    const response = await fetch(`${API_BASE}/services/stop-all`, {
      method: 'POST',
    });
    return response.json();
  },

  // Get scenarios
  getScenarios: async () => {
    try {
      const response = await fetch(`${API_BASE}/scenarios`);
      if (!response.ok) {
        throw new Error(`Server error: ${response.status}`);
      }
      const text = await response.text();
      if (!text) {
        return { success: false, error: 'Empty response' };
      }
      return JSON.parse(text);
    } catch (error) {
      console.error('Error fetching scenarios:', error);
      return { success: false, error: error.message, scenarios: [] };
    }
  },

  // Get scenario config
  getScenario: async (name) => {
    try {
      const response = await fetch(`${API_BASE}/scenarios/${name}`);
      if (!response.ok) {
        throw new Error(`Server error: ${response.status}`);
      }
      const text = await response.text();
      if (!text) {
        return { success: false, error: 'Empty response' };
      }
      return JSON.parse(text);
    } catch (error) {
      console.error('Error fetching scenario:', error);
      return { success: false, error: error.message };
    }
  },

  // Upload scenario file
  uploadScenario: async (file) => {
    try {
      const formData = new FormData();
      formData.append('scenario', file);
      const response = await fetch(`${API_BASE}/scenarios/upload`, {
        method: 'POST',
        body: formData
      });
      if (!response.ok) {
        throw new Error(`Server error: ${response.status}`);
      }
      const text = await response.text();
      if (!text) {
        return { success: false, error: 'Empty response' };
      }
      return JSON.parse(text);
    } catch (error) {
      console.error('Error uploading scenario:', error);
      return { success: false, error: error.message };
    }
  },

  // Get current running scenario
  getCurrentScenario: async () => {
    try {
      const response = await fetch(`${API_BASE}/scenarios/current`);
      if (!response.ok) {
        throw new Error(`Server error: ${response.status}`);
      }
      const text = await response.text();
      if (!text) {
        return { success: true, scenario: null };
      }
      return JSON.parse(text);
    } catch (error) {
      console.error('Error fetching current scenario:', error);
      return { success: false, error: error.message };
    }
  },

  // Start a scenario
  startScenario: async (scenario) => {
    const response = await fetch(`${API_BASE}/scenarios/start`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ scenario }),
    });
    return response.json();
  },

  // Stop current scenario
  stopScenario: async () => {
    const response = await fetch(`${API_BASE}/scenarios/stop`, {
      method: 'POST',
    });
    return response.json();
  },

  // Config launcher
  configLauncher: {
    // Get config-launcher service status
    getStatus: async () => {
      const response = await fetch(`${API_BASE}/config-launcher/status`);
      return response.json();
    },
  },

  // Simulation control
  simulation: {
    // Get simulation status
    getStatus: async () => {
      try {
        const response = await fetch(`${API_BASE}/simulation/status`);
        if (!response.ok) {
          console.warn('Simulation status endpoint returned error:', response.status);
          return { success: false, error: 'Status unavailable' };
        }
        const text = await response.text();
        if (!text) {
          return { success: false, error: 'Empty response' };
        }
        return JSON.parse(text);
      } catch (error) {
        console.error('Error fetching simulation status:', error);
        return { success: false, error: error.message };
      }
    },

    // Load scenario (Step 1: Start infrastructure services)
    load: async (scenario) => {
      try {
        const response = await fetch(`${API_BASE}/simulation/load`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ scenario }),
        });
        if (!response.ok) {
          const text = await response.text();
          let errorMsg = `Server error: ${response.status}`;
          try {
            const errorData = JSON.parse(text);
            errorMsg = errorData.error || errorMsg;
          } catch (e) {
            errorMsg = text || errorMsg;
          }
          throw new Error(errorMsg);
        }
        const text = await response.text();
        if (!text) {
          return { success: false, error: 'Empty response' };
        }
        return JSON.parse(text);
      } catch (error) {
        console.error('Error loading scenario:', error);
        return { success: false, error: error.message };
      }
    },

    // Start simulation (Step 2: Start sync-simulators)
    start: async () => {
      try {
        const response = await fetch(`${API_BASE}/simulation/start`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
        });
        if (!response.ok) {
          const text = await response.text();
          let errorMsg = `Server error: ${response.status}`;
          try {
            const errorData = JSON.parse(text);
            errorMsg = errorData.error || errorMsg;
          } catch (e) {
            errorMsg = text || errorMsg;
          }
          throw new Error(errorMsg);
        }
        const text = await response.text();
        if (!text) {
          return { success: false, error: 'Empty response' };
        }
        return JSON.parse(text);
      } catch (error) {
        console.error('Error starting simulation:', error);
        return { success: false, error: error.message };
      }
    },

    // Stop simulation
    stop: async () => {
      try {
        const response = await fetch(`${API_BASE}/simulation/stop`, {
          method: 'POST',
        });
        if (!response.ok) {
          const text = await response.text();
          let errorMsg = `Server error: ${response.status}`;
          try {
            const errorData = JSON.parse(text);
            errorMsg = errorData.error || errorMsg;
          } catch (e) {
            errorMsg = text || errorMsg;
          }
          throw new Error(errorMsg);
        }
        const text = await response.text();
        if (!text) {
          return { success: false, error: 'Empty response' };
        }
        return JSON.parse(text);
      } catch (error) {
        console.error('Error stopping simulation:', error);
        return { success: false, error: error.message };
      }
    },

    // Restart simulation
    restart: async (scenario = null) => {
      const response = await fetch(`${API_BASE}/simulation/restart`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ scenario }),
      });
      return response.json();
    },

    // Start recording
    startRecording: async (options = {}) => {
      const response = await fetch(`${API_BASE}/simulation/startRecording`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(options),
      });
      return response.json();
    },

    // Stop recording
    stopRecording: async () => {
      const response = await fetch(`${API_BASE}/simulation/stopRecording`, {
        method: 'POST',
      });
      return response.json();
    },
  },

  // Get system info
  getSystemInfo: async () => {
    const response = await fetch(`${API_BASE}/system`);
    return response.json();
  },

  // Health check
  healthCheck: async () => {
    const response = await fetch(`${API_BASE}/health`);
    return response.json();
  },

  // Carla image capture
  carla: {
    // Get list of vehicles
    getVehicles: async () => {
      const response = await fetch(`${API_BASE}/carla/vehicles`);
      return response.json();
    },

    // Capture image from spectator
    captureImage: async (options = {}) => {
      const params = new URLSearchParams(options);
      return `${API_BASE}/carla/capture?${params.toString()}`;
    },

    // Capture image from vehicle
    captureVehicleImage: async (vehicleId, options = {}) => {
      const params = new URLSearchParams(options);
      return `${API_BASE}/carla/capture/vehicle/${vehicleId}?${params.toString()}`;
    },

    // Check health
    checkHealth: async () => {
      const response = await fetch(`${API_BASE}/carla/health`);
      return response.json();
    },
  },

  // Teleoperation
  teleoperation: {
    // Start teleoperation GUI
    start: async (options = {}) => {
      try {
        const response = await fetch(`${API_BASE}/teleoperation/start`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(options),
        });
        if (!response.ok) {
          const text = await response.text();
          let errorMsg = `Server error: ${response.status}`;
          try {
            const errorData = JSON.parse(text);
            errorMsg = errorData.error || errorMsg;
          } catch (e) {
            errorMsg = text || errorMsg;
          }
          throw new Error(errorMsg);
        }
        const text = await response.text();
        if (!text) {
          return { success: false, error: 'Empty response' };
        }
        return JSON.parse(text);
      } catch (error) {
        console.error('Error starting teleoperation:', error);
        return { success: false, error: error.message };
      }
    },

    // Stop teleoperation GUI
    stop: async () => {
      try {
        const response = await fetch(`${API_BASE}/teleoperation/stop`, {
          method: 'POST',
        });
        if (!response.ok) {
          const text = await response.text();
          let errorMsg = `Server error: ${response.status}`;
          try {
            const errorData = JSON.parse(text);
            errorMsg = errorData.error || errorMsg;
          } catch (e) {
            errorMsg = text || errorMsg;
          }
          throw new Error(errorMsg);
        }
        const text = await response.text();
        if (!text) {
          return { success: false, error: 'Empty response' };
        }
        return JSON.parse(text);
      } catch (error) {
        console.error('Error stopping teleoperation:', error);
        return { success: false, error: error.message };
      }
    },

    // Get teleoperation status
    getStatus: async () => {
      try {
        const response = await fetch(`${API_BASE}/teleoperation/status`);
        if (!response.ok) {
          throw new Error(`Server error: ${response.status}`);
        }
        const text = await response.text();
        if (!text) {
          return { success: false, error: 'Empty response' };
        }
        return JSON.parse(text);
      } catch (error) {
        console.error('Error fetching teleoperation status:', error);
        return { success: false, error: error.message };
      }
    },
  },
};

// WebSocket connection
export const connectWebSocket = (onMessage) => {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  let ws;
  let destroyed = false;
  let retryTimer;

  const connect = () => {
    ws = new WebSocket(`${protocol}//${window.location.hostname}:3001`);

    ws.onopen = () => console.log('WebSocket connected');

    ws.onmessage = (event) => {
      try {
        onMessage(JSON.parse(event.data));
      } catch (e) { /* ignore parse errors */ }
    };

    ws.onerror = () => { /* close will follow, handled below */ };

    ws.onclose = () => {
      if (!destroyed) {
        retryTimer = setTimeout(connect, 3000);
      }
    };
  };

  connect();

  // Return a handle so callers can tear down cleanly
  return {
    close() {
      destroyed = true;
      clearTimeout(retryTimer);
      if (ws) ws.close();
    }
  };
};
