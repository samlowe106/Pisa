/**
 * Roster CSV import: each roster field ("Upload CSV") reads a file client-side, pulls every
 * email out of it (any CSV layout — headers/columns don't matter), and merges them into the
 * field's text input (deduped, case-insensitive) so they can still be edited before saving.
 * Loaded with `defer`.
 */
(function () {
    const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;

    function mergeEmails(existing, incoming) {
        const seen = new Set();
        const result = [];
        for (const raw of [...existing, ...incoming]) {
            const value = raw.trim();
            const key = value.toLowerCase();
            if (value && !seen.has(key)) {
                seen.add(key);
                result.push(value);
            }
        }
        return result;
    }

    document.querySelectorAll('.roster-csv input[type="file"]').forEach((fileInput) => {
        fileInput.addEventListener('change', () => {
            const file = fileInput.files && fileInput.files[0];
            const target = document.getElementById(fileInput.dataset.rosterTarget);
            if (!file || !target) {
                return;
            }
            const reader = new FileReader();
            reader.onload = () => {
                const found = String(reader.result).match(EMAIL_RE) || [];
                const existing = target.value ? target.value.split(',') : [];
                target.value = mergeEmails(existing, found).join(', ');
                fileInput.value = ''; // let the same file be re-selected later
            };
            reader.readAsText(file);
        });
    });
})();
