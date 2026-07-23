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
    