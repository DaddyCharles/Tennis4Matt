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

  const money = (n) => '$' + Number(n || 0).toFixed(2);
  const sum = (arr) => (arr || []).reduce((s, v) => s + Number(v || 0), 0);
  const avg = (arr) => (arr && arr.length) ? sum(arr) / arr.length : 0;

  function setTotals(id, html) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = html;
  }

  async function barChart(canvasId, url, label, colour) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || typeof Chart === 'undefined') return null;
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
    return d;
  }

  async function lineChart(canvasId, url, label, colour) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || typeof Chart === 'undefined') return null;
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
    return d;
  }

  async function renderCharts() {
    try {
      const d = await barChart('chart-weekly', '/api/earnings/chart/weekly', 'Weekly $', TEAL);
      if (d) setTotals('totals-weekly',
        `<span>Total <strong>${money(sum(d.data))}</strong></span><span>Avg/week <strong>${money(avg(d.data))}</strong></span>`);
    } catch (e) {}
    try {
      const d = await barChart('chart-monthly', '/api/earnings/chart/monthly', 'Monthly $', BLUE);
      if (d) {
        const best = Math.max(0, ...(d.data || [0]));
        setTotals('totals-monthly',
          `<span>Best month <strong>${money(best)}</strong></span><span>Avg/month <strong>${money(avg(d.data))}</strong></span>`);
      }
    } catch (e) {}
    try {
      const d = await lineChart('chart-daily', '/api/earnings/chart/daily', 'Daily $', TEAL);
      if (d) {
        const active = (d.data || []).filter(v => Number(v) > 0);
        setTotals('totals-daily',
          `<span>This month <strong>${money(sum(d.data))}</strong></span><span>Avg/active day <strong>${money(avg(active))}</strong></span>`);
      }
    } catch (e) {}
  }

  return { renderCharts };
})();
