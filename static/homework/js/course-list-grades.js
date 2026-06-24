/**
 * Student course list: a top toggle reveals/hides the per-course letter grades. Grades are
 * hidden by default (the cards render with `.grades-hidden`); the chosen state is remembered
 * across visits. Loaded with `defer`.
 */
(function () {
    const containers = [...document.querySelectorAll('.course-cards')];
    const toggle = document.getElementById('toggle-grades');
    if (!containers.length || !toggle) {
        return;
    }
    const KEY = 'pisa-show-course-grades';

    function apply(show) {
        containers.forEach((c) => c.classList.toggle('grades-hidden', !show));
        toggle.textContent = show ? 'Hide grades' : 'Show grades';
        toggle.setAttribute('aria-pressed', show ? 'true' : 'false');
    }

    let show = localStorage.getItem(KEY) === '1';
    apply(show);

    toggle.addEventListener('click', () => {
        show = !show;
        localStorage.setItem(KEY, show ? '1' : '0');
        apply(show);
    });
})();
