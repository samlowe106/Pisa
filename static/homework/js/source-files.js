/**
 * Shared read-only Lean source-file viewer.
 *
 * Defines the `lean` CodeMirror mode and upgrades read-only `.read-only-code` textareas
 * (imported source files, fixed code blocks) into syntax-highlighted, read-only editors,
 * with click-to-expand disclosures (the generic `.disclosure` widget) for collapsible blocks.
 *
 * Loaded on any page that sets `use_codemirror` (see homework/base.html). Self-contained
 * and idempotent: defines the mode at most once, upgrades each textarea at most once, and
 * wires the toggles at most once.
 */
(function () {
    if (typeof window.CodeMirror === 'undefined') {
        return;
    }
    const CodeMirror = window.CodeMirror;

    // Define the Lean syntax mode once. Also used by the editable editor on the problem
    // page, so this must run before that page's DOMContentLoaded handler (it does: this
    // script loads in <head>, before the inline page script).
    if (!CodeMirror.__leanModeDefined && typeof CodeMirror.defineSimpleMode === 'function') {
        CodeMirror.defineSimpleMode('lean', {
            start: [
                { regex: /--.*/, token: 'comment' },
                { regex: /--\|[\s\S]*?$/, token: 'comment' },
                { regex: /"(?:[^\\"]|\\.)*"/, token: 'string' },
                {
                    regex: /\b(?:def|theorem|lemma|example|structure|inductive|namespace|section|import|open|end|by|have|let|if|then|else|match|with|fun|lambda|forall|exists|class|instance|namespace|begin|do)\b/,
                    token: 'keyword',
                },
                { regex: /\b(?:Nat|Type|Prop|Sort|True|False|List|Option|Bool|String)\b/, token: 'atom' },
                { regex: /0x[0-9A-Fa-f]+|[0-9]+/, token: 'number' },
                { regex: /[:=\-+*/<>!]+/, token: 'operator' },
                { regex: /[\[\]{}()\.,;]/, token: 'bracket' },
            ],
            meta: { lineComment: '--' },
        });
        CodeMirror.__leanModeDefined = true;
    }

    const readonlyEditors = new WeakMap();

    function createReadOnlyEditor(textarea) {
        const editor = CodeMirror.fromTextArea(textarea, {
            mode: 'lean',
            theme: 'default',
            lineNumbers: true,
            matchBrackets: true,
            indentUnit: 2,
            indentWithTabs: false,
            tabSize: 2,
            viewportMargin: Infinity,
            readOnly: 'nocursor',
        });
        const lineHeight = typeof editor.defaultTextHeight === 'function' ? editor.defaultTextHeight() : 20;
        const height = Math.max(lineHeight * editor.lineCount() + 12, lineHeight * 3);
        editor.setSize(null, height);
        editor.getWrapperElement().classList.add('fixed-code-editor');
        return editor;
    }

    // Upgrade a textarea at most once; return the (cached) editor.
    function upgrade(textarea) {
        if (readonlyEditors.has(textarea)) {
            return readonlyEditors.get(textarea);
        }
        const editor = createReadOnlyEditor(textarea);
        readonlyEditors.set(textarea, editor);
        return editor;
    }

    function init() {
        if (document.__leanSourceFilesInit) {
            return;
        }
        document.__leanSourceFilesInit = true;

        // Upgrade all visible read-only code blocks (skip ones inside a collapsed importer).
        document.querySelectorAll('.read-only-code').forEach((textarea) => {
            if (textarea.closest('.disclosure')?.classList.contains('collapsed')) {
                return;
            }
            upgrade(textarea);
        });

        // Wire the click-to-expand disclosures. The whole header (caret + filename) toggles
        // the panel; a Hide button at the bottom collapses it. The slide itself is pure CSS;
        // we only (lazily) build/refresh CodeMirror once the panel is visible enough to measure.
        document.querySelectorAll('.disclosure').forEach((container) => {
            // Only source-file disclosures (which embed read-only code) need CodeMirror
            // wiring here; generic disclosures elsewhere are handled by disclosure.js.
            if (!container.querySelector('.read-only-code')) {
                return;
            }
            const summary = container.querySelector('.disclosure-summary');
            const hideButton = container.querySelector('.source-file-hide');

            const setExpanded = (expanded) => {
                container.classList.toggle('collapsed', !expanded);
                if (summary) {
                    summary.setAttribute('aria-expanded', expanded ? 'true' : 'false');
                }
                if (expanded) {
                    const textarea = container.querySelector('.read-only-code');
                    if (textarea) {
                        upgrade(textarea).refresh();
                    }
                }
            };

            if (summary) {
                summary.addEventListener('click', () => {
                    setExpanded(container.classList.contains('collapsed'));
                });
            }
            if (hideButton) {
                hideButton.addEventListener('click', () => setExpanded(false));
            }

            // Re-measure after the slide finishes so CodeMirror lays out at full height.
            const content = container.querySelector('.disclosure-content');
            if (content) {
                content.addEventListener('transitionend', (event) => {
                    if (event.propertyName !== 'grid-template-rows') {
                        return;
                    }
                    const textarea = container.querySelector('.read-only-code');
                    if (!container.classList.contains('collapsed') && textarea && readonlyEditors.has(textarea)) {
                        readonlyEditors.get(textarea).refresh();
                    }
                });
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
