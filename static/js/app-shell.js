// Native details elements provide keyboard-accessible submenu disclosure.
(() => {
    const sidebar = document.getElementById('sidebar');
    const toggle = document.getElementById('sidebarToggle');
    const overlay = document.getElementById('sidebarOverlay');
    if (!sidebar || !toggle || !overlay) return;
    const mobile = window.matchMedia('(max-width: 1024px)');
    const layout = sidebar.closest('.app-layout');
    const collapseToggle = document.getElementById('sidebarCollapseToggle');
    let collapsed = false;
    try { collapsed = localStorage.getItem('nexusloan.sidebarCollapsed') === 'true'; } catch (_) {}
    function syncDesktop() {
        const hidden = !mobile.matches && collapsed;
        layout.classList.toggle('sidebar-collapsed', hidden);

        collapseToggle?.setAttribute('aria-expanded', String(!hidden));
        const label = hidden ? 'Expand sidebar' : 'Collapse sidebar';
        collapseToggle?.setAttribute('aria-label', label);
        collapseToggle?.setAttribute('title', label);
    }
    collapseToggle?.addEventListener('click', () => {
        collapsed = !collapsed;
        try { localStorage.setItem('nexusloan.sidebarCollapsed', String(collapsed)); } catch (_) {}
        syncDesktop();
    });
    sidebar.querySelectorAll('.sidebar-nav > a, .sidebar-nav > details > summary, .nav-logout, .sidebar-logo').forEach(control => {
        control.title = control.textContent.trim();
    });
    sidebar.querySelectorAll('.sidebar-nav > details > summary').forEach(summary => {
        summary.addEventListener('click', event => {
            if (mobile.matches || !collapsed) return;
            event.preventDefault();
            collapsed = false;
            try { localStorage.setItem('nexusloan.sidebarCollapsed', 'false'); } catch (_) {}
            syncDesktop();
            summary.parentElement.open = true;
        });
    });
    syncDesktop();
    function setOpen(open) {
        sidebar.classList.toggle('open', open);
        overlay.classList.toggle('active', open);
        document.body.classList.toggle('sidebar-is-open', open);
        toggle.setAttribute('aria-expanded', String(open));
        if (open) sidebar.querySelector('a')?.focus();
    }
    toggle.addEventListener('click', () => setOpen(!sidebar.classList.contains('open')));
    overlay.addEventListener('click', () => { setOpen(false); toggle.focus(); });
    document.addEventListener('keydown', event => {
        if (!mobile.matches || !sidebar.classList.contains('open')) return;
        if (event.key === 'Escape') { setOpen(false); toggle.focus(); }
        if (event.key === 'Tab') {
            const controls = [...sidebar.querySelectorAll('a, button, summary')].filter(el => el.getClientRects().length);
            const first = controls[0], last = controls[controls.length - 1];
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        }
    });
    sidebar.addEventListener('click', event => {
        if (mobile.matches && event.target.closest('a')) setOpen(false);
    });
    mobile.addEventListener('change', () => { setOpen(false); syncDesktop(); });
})();
