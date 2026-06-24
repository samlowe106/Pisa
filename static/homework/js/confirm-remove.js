/**
 * "Are you sure?" guard for destructive submit buttons. Add class `js-confirm` to a
 * <button type="submit">.
 *
 * First click arms it: the label becomes "Are you sure?" and the button is disabled for a
 * 3-second countdown, so a reflexive double-click can't remove anyone. After the delay the
 * label becomes "Yes, remove" and a second click submits the form as normal. Clicking
 * anywhere else, pressing Escape, or arming a different button cancels and restores the
 * original label. Loaded with `defer`.
 */
(function () {
    const DELAY_SECONDS = 3;

    function setup(button) {
        const original = button.textContent;
        let state = 'idle'; // idle -> arming -> armed
        let timer = null;

        function reset() {
            if (timer) {
                clearInterval(timer);
                timer = null;
            }
            state = 'idle';
            button.disabled = false;
            button.classList.remove('confirm-arming', 'confirm-armed');
            button.textContent = original;
        }

        function arm() {
            state = 'arming';
            button.disabled = true;
            button.classList.add('confirm-arming');
            let remaining = DELAY_SECONDS;
            button.textContent = 'Are you sure? (' + remaining + ')';
            timer = setInterval(() => {
                remaining -= 1;
                if (remaining > 0) {
                    button.textContent = 'Are you sure? (' + remaining + ')';
                    return;
                }
                clearInterval(timer);
                timer = null;
                state = 'armed';
                button.disabled = false;
                button.classList.remove('confirm-arming');
                button.classList.add('confirm-armed');
                button.textContent = 'Yes, remove';
            }, 1000);
        }

        button.addEventListener('click', (event) => {
            if (state === 'armed') {
                return; // let the real submit go through
            }
            event.preventDefault();
            if (state === 'idle') {
                arm();
            }
        });

        // Cancel if attention moves elsewhere.
        document.addEventListener('click', (event) => {
            if (state !== 'idle' && !button.contains(event.target)) {
                reset();
            }
        });
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && state !== 'idle') {
                reset();
            }
        });
    }

    function init() {
        document.querySelectorAll('button.js-confirm').forEach(setup);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
