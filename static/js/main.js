    // ──────────────────────────────────────
    // DARK MODE TOGGLE
    // ──────────────────────────────────────
    const html = document.documentElement;
    const themeIcon = document.getElementById('themeIcon');
    const themeLabel = document.getElementById('themeLabel');
    const themeIconNav = document.getElementById('themeIconNav');

    // Load saved theme
    const savedTheme = localStorage.getItem('lm-theme') || 'light';
    html.setAttribute('data-theme', savedTheme);
    updateThemeUI(savedTheme);

    function toggleTheme() {
        const current = html.getAttribute('data-theme');
        const next = current === 'dark' ? 'light' : 'dark';
        html.setAttribute('data-theme', next);
        localStorage.setItem('lm-theme', next);
        updateThemeUI(next);
    }

    function updateThemeUI(theme) {
        const isDark = theme === 'dark';
        const iconClass = isDark ? 'fas fa-sun' : 'fas fa-moon';
        const labelText = isDark ? 'Light Mode' : 'Dark Mode';

        if (themeIcon) themeIcon.className = iconClass;
        if (themeLabel) themeLabel.textContent = labelText;
        if (themeIconNav) themeIconNav.className = iconClass;

        const mobileIcon = document.querySelector('#themeToggleMobile i');
        if (mobileIcon) mobileIcon.className = iconClass;
    }

    // Bind ALL toggle buttons
    document.getElementById('themeToggle')?.addEventListener('click', toggleTheme);
    document.getElementById('themeToggleMobile')?.addEventListener('click', toggleTheme);
    document.getElementById('themeToggleNav')?.addEventListener('click', toggleTheme);
    

    // ──────────────────────────────────────
    // AUTO-FILL INTEREST RATES BASED ON LOAN TYPE
    // ──────────────────────────────────────
    const loanTypeSelect = document.getElementById('id_loan_type');
    const rateInput = document.getElementById('id_interest_rate');

    const typicalRates = {
        'home': 8.5, 'car': 9.5, 'education': 10.0,
        'personal': 12.0, 'business': 14.0, 'gold': 11.0, 'other': 12.0
    };

    if (loanTypeSelect && rateInput) {
        loanTypeSelect.addEventListener('change', function() {
            const rate = typicalRates[this.value];
            if (rate) {
                rateInput.value = rate;
                rateInput.dispatchEvent(new Event('input')); // Trigger EMI calc
            }
        });
        // Trigger on load if type is already selected
        if (loanTypeSelect.value) {
            rateInput.value = typicalRates[loanTypeSelect.value] || rateInput.value;
        }
    }

    // ──────────────────────────────────────
    // DRAG & DROP FILE UPLOAD ENHANCEMENT
    // ──────────────────────────────────────
    const dropZone = document.getElementById('docDropZone');
    const fileInput = document.getElementById('id_file');

    if (dropZone && fileInput) {
        ['dragenter', 'dragover'].forEach(evt => {
            dropZone.addEventListener(evt, (e) => { e.preventDefault(); dropZone.classList.add('drag-over'); });
        });
        ['dragleave', 'drop'].forEach(evt => {
            dropZone.addEventListener(evt, (e) => { e.preventDefault(); dropZone.classList.remove('drag-over'); });
        });
        dropZone.addEventListener('drop', (e) => {
            fileInput.files = e.dataTransfer.files;
            updateDropZoneText(e.dataTransfer.files[0].name);
        });
        fileInput.addEventListener('change', () => {
            if(fileInput.files.length) updateDropZoneText(fileInput.files[0].name);
        });

        function updateDropZoneText(name) {
            dropZone.innerHTML = `<i class="fas fa-file-circle-check" style="font-size:32px;color:var(--primary);margin-bottom:8px;display:block;"></i> <strong>${name}</strong><br><small>Click to change</small>`;
        }
    }
    // ──────────────────────────────────────
    // LANDING PAGE — PROCESS TABS (Borrower/Lender)
    // ──────────────────────────────────────
    const procTabs = document.querySelectorAll('.proc-tab');
    const tabContents = document.querySelectorAll('.tab-content');

    procTabs.forEach(tab => {
        tab.addEventListener('click', function() {
            // Remove active from all tabs
            procTabs.forEach(t => t.classList.remove('active'));
            tabContents.forEach(c => c.classList.remove('active'));
            
            // Add active to clicked tab
            this.classList.add('active');
            
            // Show corresponding content
            const targetId = 'tab-' + this.dataset.tab;
            document.getElementById(targetId)?.classList.add('active');
        });
    });

// Accessible mobile drawer shared by borrower and admin pages.
(() => {
    const sidebar = document.getElementById('sidebar');
    const toggle = document.getElementById('sidebarToggle');
    const close = document.getElementById('sidebarClose');
    const overlay = document.getElementById('sidebarOverlay');
    const main = document.querySelector('.main-content');
    const mobile = window.matchMedia('(max-width: 1024px)');
    if (!sidebar || !toggle || !overlay) return;

    function setMenu(open, restoreFocus = true) {
        open = open && mobile.matches;
        sidebar.classList.toggle('open', open);
        overlay.classList.toggle('active', open);
        document.body.classList.toggle('menu-open', open);
        toggle.setAttribute('aria-expanded', String(open));
        toggle.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
        sidebar.inert = mobile.matches && !open;
        if (main) main.inert = open;
        if (open) close?.focus();
        else if (restoreFocus && mobile.matches) toggle.focus();
    }
    toggle.addEventListener('click', () => setMenu(!sidebar.classList.contains('open')));
    close?.addEventListener('click', () => setMenu(false));
    overlay.addEventListener('click', () => setMenu(false));
    sidebar.querySelectorAll('a').forEach(link => link.addEventListener('click', () => setMenu(false, false)));
    document.addEventListener('keydown', event => {
        if (!sidebar.classList.contains('open')) return;
        if (event.key === 'Escape') { event.preventDefault(); setMenu(false); }
        if (event.key === 'Tab') {
            const items = [...sidebar.querySelectorAll('a[href],button:not([disabled]),input:not([type="hidden"])')].filter(el => el.getClientRects().length);
            const first = items[0], last = items[items.length - 1];
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
        }
    });
    mobile.addEventListener('change', () => setMenu(false, false));
    setMenu(false, false);
})();

// The landing menu toggle must remain outside the collapsible actions.
(() => {
    const nav = document.getElementById('landingNav');
    const toggle = document.getElementById('mobileMenuBtn');
    if (!nav || !toggle) return;
    const setOpen = open => {
        nav.classList.toggle('menu-expanded', open);
        toggle.setAttribute('aria-expanded', String(open));
    };
    toggle.addEventListener('click', () => setOpen(toggle.getAttribute('aria-expanded') !== 'true'));
    nav.querySelectorAll('a').forEach(link => link.addEventListener('click', () => setOpen(false)));
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && toggle.getAttribute('aria-expanded') === 'true') {
            setOpen(false);
            toggle.focus();
        }
    });
    window.matchMedia('(max-width: 1024px)').addEventListener('change', () => setOpen(false));
})();
