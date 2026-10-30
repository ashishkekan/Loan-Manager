// Fade the divider in over the first 120px; keep it absent at the page top.
(() => {
    const nav = document.getElementById('landingNav');
    if (!nav) return;
    let scheduled = false;
    function updateBorder() {
        nav.style.setProperty('--nav-border-opacity', String(Math.min(1, Math.max(0, window.scrollY) / 120)));
        scheduled = false;
    }
    window.addEventListener('scroll', () => {
        if (!scheduled) {
            scheduled = true;
            window.requestAnimationFrame(updateBorder);
        }
    }, { passive: true });
    window.addEventListener('pageshow', updateBorder);
    updateBorder();
})();
