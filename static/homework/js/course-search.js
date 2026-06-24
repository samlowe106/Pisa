/**
 * Live filter for the course-list landing page. Typing in `#course-search` hides course
 * cards whose name doesn't contain the query (across both the active list and the "Previous
 * courses" disclosure). While searching, the Previous-courses disclosure is forced open so
 * matches there are visible; clearing the box restores its prior state. A "no matches" note
 * shows when nothing matches. Loaded with `defer`.
 */
(function () {
    function init() {
        const input = document.getElementById('course-search');
        if (!input) {
            return;
        }
        const cards = [...document.querySelectorAll('.course-card')];
        const emptyNote = document.getElementById('course-search-empty');
        const previous = document.querySelector('.disclosure.previous-courses');
        const prevSummary = previous && previous.querySelector('.disclosure-summary');
        const prevStartedCollapsed = previous
            ? previous.classList.contains('collapsed')
            : false;

        function setPreviousCollapsed(collapsed) {
            if (!previous) {
                return;
            }
            previous.classList.toggle('collapsed', collapsed);
            if (prevSummary) {
                prevSummary.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
            }
        }

        function apply() {
            const query = input.value.trim().toLowerCase();
            let visible = 0;
            cards.forEach((card) => {
                const name = (card.dataset.courseName || card.textContent || '').toLowerCase();
                const match = !query || name.includes(query);
                card.classList.toggle('search-hidden', !match);
                if (match) {
                    visible += 1;
                }
            });
            if (emptyNote) {
                emptyNote.hidden = visible !== 0;
            }
            // Open the disclosure while filtering so previous-course matches are visible.
            setPreviousCollapsed(query ? false : prevStartedCollapsed);
        }

        input.addEventListener('input', apply);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
