/**
 * Course thumbnail picker: drag-and-drop / file upload OR a preset image — mutually exclusive,
 * with a live preview. Loaded with `defer`, so the DOM is ready when this runs.
 */
(function () {
    const dropzone = document.getElementById('thumbnail-dropzone');
    if (!dropzone) {
        return;
    }
    const input = dropzone.querySelector('input[type="file"]');
    const preview = document.getElementById('thumbnail-preview');
    const presets = [...document.querySelectorAll('.thumbnail-preset input[type="radio"]')];

    function showPreview(src) {
        if (!preview) {
            return;
        }
        if (src) {
            preview.src = src;
            preview.hidden = false;
        } else {
            preview.removeAttribute('src');
            preview.hidden = true;
        }
    }

    function syncPresetSelection() {
        presets.forEach((r) =>
            r.closest('.thumbnail-preset').classList.toggle('selected', r.checked)
        );
    }

    function clearPresets() {
        presets.forEach((r) => {
            r.checked = false;
        });
        syncPresetSelection();
    }

    function useUploadedFile(file) {
        if (!file) {
            return;
        }
        showPreview(URL.createObjectURL(file));
        clearPresets();
    }

    if (input) {
        input.addEventListener('change', () => useUploadedFile(input.files && input.files[0]));
    }

    ['dragenter', 'dragover'].forEach((event) =>
        dropzone.addEventListener(event, (e) => {
            e.preventDefault();
            dropzone.classList.add('dragover');
        })
    );
    ['dragleave', 'drop'].forEach((event) =>
        dropzone.addEventListener(event, (e) => {
            e.preventDefault();
            dropzone.classList.remove('dragover');
        })
    );
    dropzone.addEventListener('drop', (e) => {
        const files = e.dataTransfer && e.dataTransfer.files;
        if (files && files.length && input) {
            input.files = files;
            useUploadedFile(files[0]);
        }
    });

    // Picking a preset clears any uploaded file.
    presets.forEach((radio) => {
        radio.addEventListener('change', () => {
            syncPresetSelection();
            if (radio.checked) {
                if (input) {
                    input.value = '';
                }
                const img = radio.closest('.thumbnail-preset').querySelector('img');
                showPreview(img ? img.src : '');
            }
        });
    });
})();
