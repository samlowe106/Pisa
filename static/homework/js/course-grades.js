/**
 * Course page — student grades table (staff only).
 *
 * Grades are hidden by default. Each row's grade cell is a toggle; a "Show all grades"
 * button at the top reveals/hides every row at once and keeps its own label in sync. The
 * Export control navigates to the download URL for the format chosen in the dropdown.
 * Loaded with `defer`, so the DOM is ready when this runs.
 */
(function () {
    function init() {
        const rows = () => [...document.querySelectorAll('.student-row')];

        function setRevealed(row, revealed) {
            row.classList.toggle('revealed', revealed);
            const toggle = row.querySelector('.grade-toggle');
            if (toggle) {
                toggle.setAttribute('aria-expanded', revealed ? 'true' : 'false');
            }
        }

        document.querySelectorAll('.grade-toggle').forEach((toggle) => {
            toggle.addEventListener('click', () => {
                const row = toggle.closest('.student-row');
                if (row) {
                    setRevealed(row, !row.classList.contains('revealed'));
                }
            });
        });

        // Separate Show all / Hide all so the table can sit in a mixed state without one
        // button having to guess which action to offer.
        const showAll = document.getElementById('show-all-grades');
        const hideAll = document.getElementById('hide-all-grades');
        if (showAll) {
            showAll.addEventListener('click', () => rows().forEach((row) => setRevealed(row, true)));
        }
        if (hideAll) {
            hideAll.addEventListener('click', () => rows().forEach((row) => setRevealed(row, false)));
        }

        const exportButton = document.getElementById('export-grades-btn');
        const exportFormat = document.getElementById('export-grades-format');
        if (exportButton && exportFormat) {
            exportButton.addEventListener('click', () => {
                if (exportFormat.value) {
                    window.location.href = exportFormat.value;
                }
            });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
