/**
 * Generic disclosure widgets.
 *
 * A `.disclosure-summary` button toggles the `collapsed` class on its `.disclosure` parent;
 * the `.disclosure-content` slides open/closed via CSS (grid-template-rows 0fr→1fr). No
 * dependencies. Loaded with `defer`, so the DOM is ready when this runs.
 */
(function () {
    function init() {
        document.querySelectorAll('.disclosure-summary').forEach((summary) => {
            summary.addEventListener('click', () => {
                const disclosure = summary.closest('.disclosure');
                if (!disclosure) {
                    return;
                }
                const collapsed = disclosure.classList.toggle('collapsed');
                summary.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
            });
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
