(() => {
 const config = JSON.parse(document.getElementById('appearance-config').textContent);
 if (config.mode === 'system') document.documentElement.dataset.theme = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
})();
