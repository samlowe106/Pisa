/**
 * Drag-and-drop reordering of an assignment's problems (staff only).
 *
 * Each problem is a row with a ≡ handle on the left. Dragging a handle reorders the rows;
 * on drop we relabel "Problem N", rewrite the position-based links, and POST the new order
 * to the server. Uses the native HTML5 Drag-and-Drop API — no dependencies. Loaded with
 * `defer` from assignment_detail.html, so the DOM is ready when this runs.
 */
(function () {
    const container = document.querySelector('.problem-reorder');
    if (!container) {
        return;
    }
    const list = container.querySelector('.problem-reorder-list');
    const reorderUrl = container.dataset.reorderUrl;
    const assignmentUrl = container.dataset.assignmentUrl;
    const tokenInput = container.querySelector('[name=csrfmiddlewaretoken]');
    if (!list || !reorderUrl) {
        return;
    }

    let dragItem = null;

    // Native DnD requires the dragged element be `draggable`. We gate that on the handle so
    // the rest of the row (the title/edit links, text selection) keeps working normally.
    container.querySelectorAll('.problem-reorder-handle').forEach((handle) => {
        const item = handle.closest('.problem-reorder-item');
        if (item) {
            handle.addEventListener('mousedown', () => {
                item.draggable = true;
            });
        }
    });

    function clearDraggable() {
        list.querySelectorAll('.problem-reorder-item').forEach((item) => {
            item.draggable = false;
        });
    }

    // A plain click on the handle (mousedown without a drag) should not leave the row armed.
    document.addEventListener('mouseup', clearDraggable);

    list.addEventListener('dragstart', (event) => {
        const item = event.target.closest('.problem-reorder-item');
        if (!item) {
            return;
        }
        dragItem = item;
        event.dataTransfer.effectAllowed = 'move';
        // Firefox won't start a drag unless some data is set.
        event.dataTransfer.setData('text/plain', item.dataset.problemId || '');
        // Defer the faded style so the drag image is the solid row.
        requestAnimationFrame(() => {
            if (dragItem) {
                dragItem.classList.add('dragging');
            }
        });
    });

    list.addEventListener('dragover', (event) => {
        if (!dragItem) {
            return;
        }
        event.preventDefault();
        event.dataTransfer.dropEffect = 'move';
        const after = getDragAfterElement(event.clientY);
        if (after === dragItem) {
            return;
        }
        if (after) {
            list.insertBefore(dragItem, after);
        } else {
            list.appendChild(dragItem);
        }
    });

    list.addEventListener('drop', (event) => event.preventDefault());

    list.addEventListener('dragend', () => {
        if (!dragItem) {
            return;
        }
        dragItem.classList.remove('dragging');
        dragItem = null;
        clearDraggable();
        persist(applyOrder());
    });

    // Find the row the cursor is currently above the midpoint of; null means "after the last".
    function getDragAfterElement(y) {
        const rows = [...list.querySelectorAll('.problem-reorder-item:not(.dragging)')];
        let closest = { offset: -Infinity, element: null };
        for (const row of rows) {
            const box = row.getBoundingClientRect();
            const offset = y - box.top - box.height / 2;
            if (offset < 0 && offset > closest.offset) {
                closest = { offset, element: row };
            }
        }
        return closest.element;
    }

    // Sync the visible "Problem N" labels and the position-based links to the new DOM order,
    // and return the new ordering of problem ids for persisting.
    function applyOrder() {
        const items = [...list.querySelectorAll('.problem-reorder-item')];
        items.forEach((item, index) => {
            const number = index + 1;
            const label = item.querySelector('.problem-reorder-index');
            if (label) {
                label.textContent = `Problem ${number}`;
            }
            const title = item.querySelector('.problem-reorder-title');
            if (title && assignmentUrl) {
                title.setAttribute('href', `${assignmentUrl}${number}/`);
            }
            const edit = item.querySelector('.problem-reorder-edit');
            if (edit && assignmentUrl) {
                edit.setAttribute('href', `${assignmentUrl}${number}/edit/`);
            }
        });
        return items.map((item) => Number(item.dataset.problemId));
    }

    function persist(order) {
        container.classList.remove('reorder-error');
        fetch(reorderUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': tokenInput ? tokenInput.value : '',
            },
            body: JSON.stringify({ order }),
        })
            .then((response) => {
                if (!response.ok) {
                    throw new Error('Reorder failed');
                }
            })
            .catch(() => {
                container.classList.add('reorder-error');
            });
    }
})();
