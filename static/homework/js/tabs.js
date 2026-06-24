/**
 * Generic tab strip. Markup contract:
 *
 *   <div data-tabs>
 *     <div class="tab-nav" role="tablist">
 *       <button class="tab-link" role="tab" data-tab="foo" aria-controls="tab-foo">Foo</button>
 *       ...
 *     </div>
 *     <section class="tab-panel" data-tab-panel="foo" id="tab-foo" role="tabpanel">...</section>
 *     ...
 *   </div>
 *
 * The active tab is mirrored to the URL hash so a reload or deep link reopens it. Server
 * markup should mark the first link/panel `is-active` so the right one shows before this
 * runs (and if JS is unavailable). Loaded with `defer`.
 */
(function () {
    function setup(container) {
        const links = [...container.querySelectorAll('.tab-link')];
        const panels = [...container.querySelectorAll('.tab-panel')];
        if (!links.length) {
            return;
        }

        function activate(name, updateHash) {
            links.forEach((link) => {
                const on = link.dataset.tab === name;
                link.classList.toggle('is-active', on);
                link.setAttribute('aria-selected', on ? 'true' : 'false');
                link.tabIndex = on ? 0 : -1;
            });
            panels.forEach((panel) => {
                panel.classList.toggle('is-active', panel.dataset.tabPanel === name);
            });
            if (updateHash) {
                history.replaceState(null, '', '#' + name);
            }
        }

        links.forEach((link) => {
            link.addEventListener('click', () => activate(link.dataset.tab, true));
        });

        // Arrow-key navigation across the tab strip (standard ARIA tabs behaviour).
        container.querySelector('.tab-nav').addEventListener('keydown', (event) => {
            const current = links.indexOf(document.activeElement);
            if (current === -1) {
                return;
            }
            let next = null;
            if (event.key === 'ArrowRight') {
                next = (current + 1) % links.length;
            } else if (event.key === 'ArrowLeft') {
                next = (current - 1 + links.length) % links.length;
            }
            if (next !== null) {
                event.preventDefault();
                links[next].focus();
                activate(links[next].dataset.tab, true);
            }
        });

        // No hash? Honour whichever tab the server marked active (falling back to the first).
        const fromHash = decodeURIComponent(location.hash.replace('#', ''));
        const known = links.some((link) => link.dataset.tab === fromHash);
        const serverActive = links.find((link) => link.classList.contains('is-active'));
        const fallback = serverActive ? serverActive.dataset.tab : links[0].dataset.tab;
        activate(known ? fromHash : fallback, false);
    }

    function init() {
        document.querySelectorAll('[data-tabs]').forEach(setup);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
