import { useState, useEffect, useCallback } from "react";
import "@/App.css";
import axios from "axios";
import { 
  Cpu, 
  MemoryStick, 
  Thermometer, 
  Activity, 
  Container, 
  ArrowDownToLine, 
  ArrowUpFromLine,
  Server,
  Clock,
  RefreshCw,
  HardDrive,
  RotateCcw
} from "lucide-react";
import { 
  LineChart, 
  Line, 
  ResponsiveContainer,
  Tooltip
} from "recharts";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL || '';
const API = `${BACKEND_URL}/api`;

// Format uptime
const formatUptime = (hours) => {
  const days = Math.floor(hours / 24);
  const h = Math.floor(hours % 24);
  if (days > 0) return `${days}d ${h}h`;
  const minutes = Math.floor((hours % 1) * 60);
  return `${h}h ${minutes}m`;
};

// Format container uptime
const formatContainerUptime = (seconds) => {
  if (!seconds || seconds <= 0) return '-';
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
};

// Get status color
const getStatusColor = (percent) => {
  if (percent > 80) return '#FF3366';
  if (percent > 60) return '#FFCC00';
  return '#00FF66';
};

// Metric Card Component
const MetricCard = ({ title, value, unit, icon: Icon, color = "#00E5FF", subtitle, testId }) => (
  <div 
    className="metric-card bg-[#0A0A0A] border border-[#1A1A1A] rounded-sm p-4"
    data-testid={testId}
  >
    <div className="flex items-center justify-between mb-3">
      <span className="metric-label">{title}</span>
      <Icon size={16} className="text-[#8A8A8A]" strokeWidth={1.5} />
    </div>
    <div className="flex items-baseline gap-1">
      <span className="metric-value text-4xl md:text-5xl" style={{ color }}>
        {value ?? '-'}
      </span>
      <span className="text-[#8A8A8A] text-lg font-mono">{unit}</span>
    </div>
    {subtitle && (
      <div className="mt-2 text-xs text-[#8A8A8A] font-mono">{subtitle}</div>
    )}
  </div>
);

// Mini Chart Component
const MiniChart = ({ data, dataKey, color }) => (
  <div className="h-16">
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data}>
        <Line 
          type="monotone" 
          dataKey={dataKey} 
          stroke={color} 
          strokeWidth={1.5}
          dot={false}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: '#0A0A0A',
            border: '1px solid #1A1A1A',
            borderRadius: '4px',
            fontFamily: 'JetBrains Mono',
            fontSize: '12px'
          }}
          labelStyle={{ color: '#8A8A8A' }}
        />
      </LineChart>
    </ResponsiveContainer>
  </div>
);

// Container Card Component
const ContainerCard = ({ container }) => {
  const isRunning = container.status === 'running';
  
  return (
    <div 
      className="metric-card bg-[#0A0A0A] border border-[#1A1A1A] rounded-sm p-4"
      data-testid={`container-${container.name}`}
    >
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Container size={16} className="text-[#8A8A8A]" strokeWidth={1.5} />
          <span className="font-medium text-[#EDEDED]">{container.name}</span>
        </div>
        <div className="flex items-center gap-2">
          <span 
            className={`w-2 h-2 rounded-full ${isRunning ? 'bg-[#00FF66] live-indicator' : 'bg-[#FF3366]'}`}
          />
          <span className={`text-xs font-mono uppercase ${isRunning ? 'text-[#00FF66]' : 'text-[#FF3366]'}`}>
            {container.status}
          </span>
        </div>
      </div>

      {isRunning && (
        <div className="space-y-4">
          {/* CPU */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-mono uppercase tracking-wider text-[#8A8A8A]">CPU</span>
              <span className="text-sm font-mono" style={{ color: getStatusColor(container.cpu.usage_percent) }}>
                {container.cpu.usage_percent}%
              </span>
            </div>
            <div className="h-1 bg-[#1A1A1A] rounded-full overflow-hidden">
              <div 
                className="h-full rounded-full transition-all duration-500"
                style={{ 
                  width: `${Math.min(100, container.cpu.usage_percent)}%`,
                  backgroundColor: getStatusColor(container.cpu.usage_percent)
                }}
              />
            </div>
          </div>

          {/* Memory */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-mono uppercase tracking-wider text-[#8A8A8A]">RAM</span>
              <span className="text-sm font-mono" style={{ color: getStatusColor(container.memory.usage_percent) }}>
                {container.memory.usage_mb} MB
              </span>
            </div>
            <div className="h-1 bg-[#1A1A1A] rounded-full overflow-hidden">
              <div 
                className="h-full rounded-full transition-all duration-500"
                style={{ 
                  width: `${Math.min(100, container.memory.usage_percent)}%`,
                  backgroundColor: getStatusColor(container.memory.usage_percent)
                }}
              />
            </div>
          </div>

          {/* Network & Uptime */}
          <div className="flex justify-between pt-2 border-t border-[#1A1A1A]">
            <div className="flex gap-3">
              <div className="flex items-center gap-1">
                <ArrowDownToLine size={12} className="text-[#00E5FF]" strokeWidth={1.5} />
                <span className="text-xs font-mono text-[#8A8A8A]">
                  {(container.network.rx_bytes / 1024 / 1024).toFixed(1)}MB
                </span>
              </div>
              <div className="flex items-center gap-1">
                <ArrowUpFromLine size={12} className="text-[#C51A4A]" strokeWidth={1.5} />
                <span className="text-xs font-mono text-[#8A8A8A]">
                  {(container.network.tx_bytes / 1024 / 1024).toFixed(1)}MB
                </span>
              </div>
            </div>
            <div className="flex items-center gap-2">
              {container.restart_count > 0 && (
                <div className="flex items-center gap-1" title="Restarts">
                  <RotateCcw size={10} className="text-[#FFCC00]" strokeWidth={1.5} />
                  <span className="text-xs font-mono text-[#FFCC00]">{container.restart_count}</span>
                </div>
              )}
              <span className="text-xs font-mono text-[#8A8A8A]">
                {formatContainerUptime(container.uptime_seconds)}
              </span>
            </div>
          </div>
        </div>
      )}

      {!isRunning && (
        <div className="text-center py-4 text-[#8A8A8A] text-sm">
          Container gestoppt
        </div>
      )}
    </div>
  );
};

function App() {
  const [hostMetrics, setHostMetrics] = useState(null);
  const [containers, setContainers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [lastUpdate, setLastUpdate] = useState(null);
  const [cpuHistory, setCpuHistory] = useState([]);
  const [ramHistory, setRamHistory] = useState([]);
  const [refreshInterval, setRefreshInterval] = useState(3000);

  const fetchMetrics = useCallback(async () => {
    try {
      const response = await axios.get(`${API}/metrics/all`);
      const { host, containers: containerData } = response.data;
      
      setHostMetrics(host);
      setContainers(containerData);
      setLastUpdate(new Date());
      
      setCpuHistory(prev => {
        const newData = [...prev, { time: new Date().toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' }), value: host.cpu.usage_percent }];
        return newData.slice(-20);
      });
      
      setRamHistory(prev => {
        const newData = [...prev, { time: new Date().toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' }), value: host.memory.usage_percent }];
        return newData.slice(-20);
      });
      
      setLoading(false);
    } catch (error) {
      console.error('Error fetching metrics:', error);
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchMetrics();
    const interval = setInterval(fetchMetrics, refreshInterval);
    return () => clearInterval(interval);
  }, [fetchMetrics, refreshInterval]);

  if (loading) {
    return (
      <div className="min-h-screen bg-[#050505] flex items-center justify-center">
        <div className="flex items-center gap-3">
          <RefreshCw className="animate-spin text-[#00E5FF]" size={24} />
          <span className="text-[#8A8A8A] font-mono">Lade Metriken...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#050505]" data-testid="dashboard">
      {/* Header */}
      <header className="sticky top-0 z-50 border-b border-[#1A1A1A] bg-[#050505]/90 backdrop-blur-xl">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <Server size={24} className="text-[#C51A4A]" strokeWidth={1.5} />
              <h1 className="text-xl md:text-2xl font-medium tracking-tight text-white">
                Pi Monitor
              </h1>
              <div className="flex items-center gap-2 ml-4">
                <span className="w-2 h-2 rounded-full bg-[#00FF66] live-indicator" />
                <span className="text-xs font-mono text-[#8A8A8A] uppercase tracking-wider">Live</span>
              </div>
            </div>
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-2 text-[#8A8A8A]">
                <Clock size={14} strokeWidth={1.5} />
                <span className="text-xs font-mono">
                  {lastUpdate?.toLocaleTimeString('de-DE')}
                </span>
              </div>
              <select 
                value={refreshInterval}
                onChange={(e) => setRefreshInterval(Number(e.target.value))}
                className="bg-[#0A0A0A] border border-[#1A1A1A] rounded-sm px-2 py-1 text-xs font-mono text-[#8A8A8A] focus:outline-none focus:border-[#00E5FF]"
                data-testid="refresh-interval-select"
              >
                <option value={2000}>2s</option>
                <option value={3000}>3s</option>
                <option value={5000}>5s</option>
                <option value={10000}>10s</option>
              </select>
            </div>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-6">
        {/* Host Section */}
        <section className="mb-8" data-testid="host-section">
          <div className="flex items-center gap-2 mb-4">
            <h2 className="text-lg font-medium text-white">Raspberry Pi</h2>
            <span className="text-xs font-mono text-[#8A8A8A] bg-[#0A0A0A] px-2 py-1 rounded-sm border border-[#1A1A1A]">
              {hostMetrics?.hostname}
            </span>
          </div>

          {/* Host Metrics Grid - 5 columns */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4 mb-6">
            <MetricCard
              title="CPU"
              value={hostMetrics?.cpu.usage_percent}
              unit="%"
              icon={Cpu}
              color={getStatusColor(hostMetrics?.cpu.usage_percent || 0)}
              subtitle={`${hostMetrics?.cpu.cores} Cores @ ${hostMetrics?.cpu.frequency_mhz} MHz`}
              testId="host-cpu-metric"
            />
            <MetricCard
              title="RAM"
              value={hostMetrics?.memory.usage_percent}
              unit="%"
              icon={MemoryStick}
              color={getStatusColor(hostMetrics?.memory.usage_percent || 0)}
              subtitle={`${hostMetrics?.memory.used_mb} / ${hostMetrics?.memory.total_mb} MB`}
              testId="host-ram-metric"
            />
            <MetricCard
              title="Disk"
              value={hostMetrics?.disk?.usage_percent}
              unit="%"
              icon={HardDrive}
              color={getStatusColor(hostMetrics?.disk?.usage_percent || 0)}
              subtitle={`${hostMetrics?.disk?.used_gb} / ${hostMetrics?.disk?.total_gb} GB`}
              testId="host-disk-metric"
            />
            <MetricCard
              title="Load"
              value={hostMetrics?.load_average?.['1min']}
              unit=""
              icon={Activity}
              color="#00E5FF"
              subtitle={`5m: ${hostMetrics?.load_average?.['5min']} | 15m: ${hostMetrics?.load_average?.['15min']}`}
              testId="host-load-metric"
            />
            <MetricCard
              title="Temp"
              value={hostMetrics?.temperature?.celsius}
              unit="°C"
              icon={Thermometer}
              color={hostMetrics?.temperature?.celsius > 70 ? '#FF3366' : hostMetrics?.temperature?.celsius > 60 ? '#FFCC00' : '#00FF66'}
              subtitle={`Uptime: ${formatUptime(hostMetrics?.uptime_hours || 0)}`}
              testId="host-temp-metric"
            />
          </div>

          {/* Charts + Swap */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="bg-[#0A0A0A] border border-[#1A1A1A] rounded-sm p-4" data-testid="cpu-chart">
              <div className="flex items-center justify-between mb-3">
                <span className="text-xs font-mono uppercase tracking-wider text-[#8A8A8A]">CPU Verlauf</span>
                <span className="text-sm font-mono text-[#C51A4A]">{hostMetrics?.cpu.usage_percent}%</span>
              </div>
              <MiniChart data={cpuHistory} dataKey="value" color="#C51A4A" />
            </div>
            <div className="bg-[#0A0A0A] border border-[#1A1A1A] rounded-sm p-4" data-testid="ram-chart">
              <div className="flex items-center justify-between mb-3">
                <span className="text-xs font-mono uppercase tracking-wider text-[#8A8A8A]">RAM Verlauf</span>
                <span className="text-sm font-mono text-[#00FF66]">{hostMetrics?.memory.usage_percent}%</span>
              </div>
              <MiniChart data={ramHistory} dataKey="value" color="#00FF66" />
            </div>
            {hostMetrics?.memory?.swap_total_mb > 0 && (
              <div className="bg-[#0A0A0A] border border-[#1A1A1A] rounded-sm p-4" data-testid="swap-info">
                <div className="flex items-center justify-between mb-3">
                  <span className="text-xs font-mono uppercase tracking-wider text-[#8A8A8A]">Swap</span>
                  <span className="text-sm font-mono" style={{ color: getStatusColor(hostMetrics?.memory?.swap_percent || 0) }}>
                    {hostMetrics?.memory?.swap_percent}%
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-2xl font-mono text-white">{hostMetrics?.memory?.swap_used_mb} MB</span>
                  <span className="text-xs font-mono text-[#8A8A8A]">/ {hostMetrics?.memory?.swap_total_mb} MB</span>
                </div>
                <div className="h-1 bg-[#1A1A1A] rounded-full overflow-hidden mt-3">
                  <div 
                    className="h-full rounded-full transition-all duration-500"
                    style={{ 
                      width: `${hostMetrics?.memory?.swap_percent || 0}%`,
                      backgroundColor: getStatusColor(hostMetrics?.memory?.swap_percent || 0)
                    }}
                  />
                </div>
              </div>
            )}
          </div>
        </section>

        {/* Containers Section */}
        <section data-testid="containers-section">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-medium text-white">Docker Container</h2>
              <span className="text-xs font-mono text-[#8A8A8A] bg-[#0A0A0A] px-2 py-1 rounded-sm border border-[#1A1A1A]">
                {containers.filter(c => c.status === 'running').length} / {containers.length} aktiv
              </span>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {containers.map((container) => (
              <ContainerCard key={container.id} container={container} />
            ))}
          </div>
        </section>
      </main>

      {/* Footer */}
      <footer className="border-t border-[#1A1A1A] mt-8">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <div className="flex items-center justify-between text-xs font-mono text-[#8A8A8A]">
            <span>Pi Monitor v1.1</span>
            <span>{hostMetrics?.process_count} Prozesse</span>
          </div>
        </div>
      </footer>
    </div>
  );
}

export default App;
