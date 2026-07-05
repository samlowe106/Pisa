/**
 * Apply grade-bar heights from data attributes (stats tab).
 *
 * Each bar carries `data-height="<percent>"` instead of an inline `style="height:…%"`, so the
 * Content-Security-Policy can forbid inline styles (`style-src` without 'unsafe-inline'). Setting
 * an individual property via `el.style.height` from JS is *not* governed by `style-src` (only
 * declarative `style=` attributes and `<style>` blocks are), so this stays CSP-clean. Loaded with
 * `defer`, so the DOM is ready when this runs.
 */
(function () {
    document.querySelectorAll('.grade-bar[data-height]').forEach((bar) => {
        bar.style.height = bar.dataset.height + '%';
    });
})();
