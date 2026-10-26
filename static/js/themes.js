(() => {
 let config = JSON.parse(document.getElementById('appearance-config').textContent);
 const root = document.documentElement, dialog = document.getElementById('themeStudio');
 const media = matchMedia('(prefers-color-scheme: dark)');
 const byId = id => document.getElementById(id);
 function apply() {
  root.dataset.palette = config.palette; root.dataset.font = config.font;
  root.dataset.theme = config.mode === 'system' ? (media.matches ? 'dark' : 'light') : config.theme;
  document.dispatchEvent(new CustomEvent('themechange'));
 }
 media.addEventListener('change', apply);
 if (!dialog) return;
 function sync() {
  dialog.querySelectorAll('[data-palette-choice]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.paletteChoice === config.selection)));
  byId('themeFont').value = config.fontSelection; byId('themeMode').value = config.mode;
  byId('themePersonalControls').hidden = !config.editable; byId('themePolicyNotice').hidden = config.editable;
  if (byId('workspacePalette')) {
   byId('workspacePalette').value = config.defaultPalette; byId('workspaceFont').value = config.defaultFont;
   byId('allowPersonalThemes').checked = config.allowPersonal;
  }
 }
 let busy = false;
 async function save(data) {
  if (busy) return; busy = true;
  const focused = document.activeElement;
  const controls = [...dialog.querySelectorAll('button,select,input')].filter(el => el.id !== 'closeThemeStudio');
  controls.forEach(el => el.disabled = true); byId('themeSaveStatus').textContent = 'Saving appearance…';
  try {
   const response = await fetch(byId('themeCsrf').action, {method:'POST', credentials:'same-origin', headers:{'X-CSRFToken':byId('themeCsrf').querySelector('input').value}, body:new URLSearchParams(data)});
   const result = await response.json(); if (!response.ok) throw new Error(result.error || 'Could not save appearance.');
   config = result; apply(); sync();
   byId('themeSaveStatus').textContent = data.scope === 'workspace' ? 'Workspace default saved. Personal choices are preserved.' : 'Saved. Your appearance now applies across every page.';
  } catch (error) { sync(); byId('themeSaveStatus').textContent = 'Unable to save. Please try again. ' + error.message; }
  finally { controls.forEach(el => el.disabled = false); busy = false; if (dialog.open && focused?.isConnected) focused.focus(); }
 }
 function personal(palette, mode = byId('themeMode').value) {
  save({scope:'personal', palette, font:byId('themeFont').value, mode});
 }
 document.querySelectorAll('[data-theme-open],#themeToggle,#themeToggleMobile,#themeToggleNav').forEach(button => button.addEventListener('click', () => {sync(); dialog.showModal();}));
 byId('closeThemeStudio').addEventListener('click', () => dialog.close());
 dialog.querySelectorAll('[data-palette-choice]').forEach(b => b.addEventListener('click', () => personal(b.dataset.paletteChoice, 'palette')));
 byId('themeFont').addEventListener('change', () => personal(config.palette));
 byId('themeMode').addEventListener('change', () => personal(config.palette));
 byId('themeReset').addEventListener('click', () => personal('inherit', 'palette'));
 byId('saveWorkspaceTheme')?.addEventListener('click', () => save({scope:'workspace', palette:byId('workspacePalette').value, font:byId('workspaceFont').value, allow_personal:String(byId('allowPersonalThemes').checked)}));
 sync();
})();
