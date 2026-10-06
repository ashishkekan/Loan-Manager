/* Shared interactions. Native forms remain usable without JavaScript. */
(() => {
    const html = document.documentElement;
    function setTheme(theme) {
        html.dataset.theme = theme === 'dark' ? 'dark' : 'light';
        const dark = html.dataset.theme === 'dark';
        document.querySelectorAll('#themeIcon, #themeIconNav, #themeToggleMobile i').forEach(icon => icon.className = dark ? 'fas fa-sun' : 'fas fa-moon');
        const label = document.getElementById('themeLabel');
        if (label) label.textContent = dark ? 'Light mode' : 'Dark mode';
        try { localStorage.setItem('lm-theme', html.dataset.theme); } catch (_) {}
    }
    try { setTheme(localStorage.getItem('lm-theme') || 'light'); } catch (_) { setTheme('light'); }
    document.querySelectorAll('#themeToggle, #themeToggleMobile, #themeToggleNav').forEach(button => button.addEventListener('click', () => setTheme(html.dataset.theme === 'dark' ? 'light' : 'dark')));
    const sidebar = document.getElementById('sidebar');
    const toggle = document.getElementById('sidebarToggle');
    const overlay = document.getElementById('sidebarOverlay');
    const smallScreen = matchMedia('(max-width:1024px)');
    function menu(open, restore = true) {
        sidebar?.classList.toggle('open', open);
        overlay?.classList.toggle('active', open);
        toggle?.setAttribute('aria-expanded', String(open));
        toggle?.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
        document.body.classList.toggle('menu-open', open);
        if (sidebar) sidebar.inert = smallScreen.matches && !open;
        if (open) sidebar?.querySelector('a')?.focus();
        else if (restore && smallScreen.matches) toggle?.focus();
    }
    menu(false, false);
    toggle?.addEventListener('click', () => menu(!sidebar.classList.contains('open')));
    overlay?.addEventListener('click', () => menu(false));
    smallScreen.addEventListener('change', () => menu(false, false));
    document.addEventListener('keydown', event => {
        if (!sidebar?.classList.contains('open')) return;
        if (event.key === 'Escape') menu(false);
        if (event.key === 'Tab') {
            const items = [...sidebar.querySelectorAll('a,button,input')].filter(el => !el.disabled && el.offsetParent !== null);
            if (!items.length) return;
            if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items.at(-1).focus(); }
            else if (!event.shiftKey && document.activeElement === items.at(-1)) { event.preventDefault(); items[0].focus(); }
        }
    });
    const path = location.pathname;
    document.querySelectorAll('.sidebar-nav .nav-item').forEach(link => {
        const active = path === link.pathname || (link.pathname !== '/dashboard/' && path.startsWith(link.pathname));
        link.classList.toggle('active', active);
        if (active) link.setAttribute('aria-current','page');
    });
    document.querySelectorAll('.alert-close').forEach(button => button.addEventListener('click', () => button.closest('.alert').remove()));
    document.querySelectorAll('form[method="post"]').forEach(form => form.addEventListener('submit', event => {
        if (event.defaultPrevented) return;
        if (form.dataset.submitting === 'true') { event.preventDefault(); return; }
        if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) { event.preventDefault(); return; }
        form.dataset.submitting = 'true';
        // Delay so the clicked submitter's name/value remains in the request.
        setTimeout(() => form.querySelectorAll('button[type="submit"],button:not([type])').forEach(button => { button.disabled = true; button.setAttribute('aria-busy','true'); }), 0);
    }));
    window.addEventListener('pageshow', () => document.querySelectorAll('form').forEach(form => {
        delete form.dataset.submitting;
        form.querySelectorAll('[aria-busy="true"]').forEach(button => { button.disabled = false; button.removeAttribute('aria-busy'); });
    }));
    document.querySelectorAll('table').forEach(table => {
        if (!table.closest('.table-responsive,.admin-table-wrapper,.table-wrapper,.report-table-wrapper')) {
            const wrap = document.createElement('div'); wrap.className = 'table-responsive';
            table.before(wrap); wrap.append(table);
        }
    });
    document.querySelectorAll('.proc-tab').forEach(tab => tab.addEventListener('click', () => {
        document.querySelectorAll('.proc-tab,.tab-content').forEach(el => el.classList.remove('active'));
        tab.classList.add('active'); document.getElementById('tab-' + tab.dataset.tab)?.classList.add('active');
    }));
})();
