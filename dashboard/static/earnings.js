/* Ivan — earnings charts (Chart.js dark theme). */

const Earnings = (() => {
  const TEAL = '#00d4aa';
  const BLUE = '#0080ff';

  const chartDefaults = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { labels: { color: 'rgba(255,255,255,0.6)', font: { size: 12 } } },
      tooltip: {
        backgroundColor: 'rgba(13,31,56,0.95)',
        borderColor: 'rgba(255,255,255,0.1)',
        borderWidth: 1,
        titleColor: '#ffffff',
        bodyColor: 'rgba(255,255,255,0.7)',
      },
    },
    scales: {
      x: { ticks: { color: 'rgba(255,255,255,0.4)' }, grid: { color: 'rgba(255,255,255,0.04)' } },
      y: {
        ticks: { color: 'rgba(255,255,255,0.4)', callback: v => '$' + v },
        grid: { color: 'rgba(255,255,255,0.04)' },
      },
    },
  };

  const _charts = {};

  function destroy(id) {
    if (_charts[id]) { _charts[id].destroy(); delete _charts[id]; }
  }

  async function barChart(canvasId, url, label, colour) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || typeof Chart === 'undefined') return;
    const d = await Coach.getJSON(url);
    destroy(canvasId);
    _charts[canvasId] = new Chart(canvas, {
      type: 'bar',
      data: {
        labels: d.labels || [],
        datasets: [{
          label,
          data: d.data || [],
          backgroundColor: colour,
          borderRadius: 6,
          maxBarThickness: 36,
        }],
      },
      options: chartDefaults,
    });
  }

  async function lineChart(canvasId, url, label, colour) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || typeof Chart === 'undefined') return;
    const d = await Coach.getJSON(url);
    destroy(canvasId);
    _charts[canvasId] = new Chart(canvas, {
      type: 'line',
      data: {
        labels: d.labels || [],
        datasets: [{
          label,
          data: d.data || [],
          borderColor: colour,
          backgroundColor: 'rgba(0,212,170,0.12)',
          fill: true,
          tension: 0.3,
          pointRadius: 2,
        }],
      },
      options: chartDefaults,
    });
  }

  async function renderCharts() {
    try { await barChart('chart-weekly', '/api/earnings/chart/weekly', 'Weekly $', TEAL); } catch (e) {}
    try { await barChart('chart-monthly', '/api/earnings/chart/monthly', 'Monthly $', BLUE); } catch (e) {}
    try { await lineChart('chart-daily', '/api/earnings/chart/daily', 'Daily $', TEAL); } catch (e) {}
  }

  return { renderCharts };
})();
