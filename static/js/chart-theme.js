/* A shared financial-series palette; values come from validated server presets. */
(() => {
    const node = document.getElementById('site-chart-palette');
    const colors = node ? JSON.parse(node.textContent) : {
        series: ['#22863a', '#b45309', '#1d4ed8', '#7e22ce'],
        neutral: '#94a3b8', grid: '#e2e8f0', text: '#64748b'
    };
    colors.alpha = (hex, opacity) => {
        const rgb = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
        return `rgba(${rgb.join(',')},${opacity})`;
    };
    window.LoanChartTheme = colors;
    if (!window.Chart) return;
    Chart.defaults.color = colors.text;
    Chart.defaults.font.family = "'Plus Jakarta Sans', sans-serif";
    Chart.defaults.font.size = 11;
    Chart.defaults.plugins.legend.labels.usePointStyle = true;
    Chart.defaults.plugins.legend.labels.padding = 16;
    Chart.defaults.plugins.tooltip.backgroundColor = '#0f172a';
    Chart.defaults.plugins.tooltip.titleColor = '#ffffff';
    Chart.defaults.plugins.tooltip.bodyColor = '#e2e8f0';
    // Bar datasets use the same ordered series colors as the line and ring charts.
    Chart.register({
        id: 'loanBarColors',
        beforeInit(chart) {
            chart.data.datasets.forEach((dataset, index) => {
                if ((dataset.type || chart.config.type) !== 'bar') return;
                const color = colors.series[index % colors.series.length];
                dataset.backgroundColor ??= colors.alpha(color, 0.85);
                dataset.borderColor ??= color;
                dataset.hoverBackgroundColor ??= color;
                dataset.borderRadius ??= 4;
            });
        }
    });
})();
