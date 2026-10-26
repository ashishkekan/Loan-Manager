(() => {
 const data = JSON.parse(document.getElementById('repayment-trend').textContent);
 const range = document.getElementById('trendRange'), bars = document.getElementById('repaymentBars');
 const money = value => new Intl.NumberFormat('en-IN', {style:'currency',currency:'INR',maximumFractionDigits:2}).format(value);
 function render() {
  const rows = data.slice(-Number(range.value)), max = Math.max(...rows.map(r => Number(r.amount)), 1);
  bars.replaceChildren();
  let total = 0;
  rows.forEach(row => {
   const value = Number(row.amount); total += value;
   const column = document.createElement('div'); column.className = 'trend-column'; column.tabIndex = 0;
   column.setAttribute('aria-label', row.label + ': ' + money(value)); column.title = row.label + ': ' + money(value);
   const bar = document.createElement('div'); bar.className = 'trend-bar'; bar.style.height = (value / max * 100) + '%';
   const label = document.createElement('small'); label.textContent = row.label.split(' ')[0];
   column.append(bar,label); bars.append(column);
   const show = () => document.getElementById('trendCaption').textContent = row.label + ' · ' + money(value) + ' recorded repayments';
   column.addEventListener('mouseenter', show); column.addEventListener('focus', show);
  });
  document.getElementById('trendTotal').textContent = money(total);
  document.getElementById('trendCaption').textContent = total ? 'Recorded installments and prepayments. Focus or hover on a month for details.' : 'No recorded repayments in this period.';
 }
 range.addEventListener('change', render); render();
 document.getElementById('portfolioSearch').addEventListener('input', event => {
  const query = event.target.value.trim().toLowerCase(); let count = 0;
  document.querySelectorAll('#portfolioTable tbody tr').forEach(row => {row.hidden = !row.textContent.toLowerCase().includes(query); if (!row.hidden) count++;});
  const status = document.getElementById('portfolioSearchStatus'); status.className = query ? 'trend-caption' : 'sr-only'; status.textContent = count + ' matching rows in recent loans';
 });
})();
