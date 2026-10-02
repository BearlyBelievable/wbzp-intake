(function () {
    function initForm(form) {
        const submitButton = form.querySelector('button[type="submit"]');
        const message = form.querySelector('.wbzp-intake-message');
        const submitButtonText = submitButton.textContent;

        function fieldValue(name) {
            const checked = form.querySelectorAll(`[name="${name}"]:checked`);
            if (checked.length) {
                if (checked[0].type === 'checkbox') {
                    return Array.from(checked).map((el) => el.value);
                }
                return checked[0].value;
            }
            const select = form.querySelector(`select[name="${name}"]`);
            return select ? select.value : '';
        }

        function syncCompanion(companion) {
            const fieldName = companion.getAttribute('data-wbzp-intake-companion-of');
            const expected = companion.getAttribute('data-wbzp-intake-companion-when');
            const value = fieldValue(fieldName);
            const show = Array.isArray(value) ? value.includes(expected) : value === expected;
            companion.hidden = !show;
            companion.querySelectorAll('input, textarea, select').forEach((input) => {
                if (input.dataset.wbzpIntakeRequired === 'true') {
                    input.required = show;
                }
            });
        }

        form.querySelectorAll('[data-wbzp-intake-companion-of]').forEach((companion) => {
            const fieldName = companion.getAttribute('data-wbzp-intake-companion-of');
            form.querySelectorAll(`[name="${fieldName}"]`).forEach((input) => {
                input.addEventListener('change', () => syncCompanion(companion));
            });
            syncCompanion(companion);
        });

        form.querySelectorAll('[data-wbzp-intake-multiselect-required]').forEach((group) => {
            const boxes = group.querySelectorAll('input[type="checkbox"]');
            function syncMultiselectValidity() {
                const anyChecked = Array.from(boxes).some((box) => box.checked);
                boxes.forEach((box, index) => {
                    box.setCustomValidity(!anyChecked && index === 0 ? 'Please select at least one option.' : '');
                });
            }
            boxes.forEach((box) => box.addEventListener('change', syncMultiselectValidity));
            syncMultiselectValidity();
        });

        function updateCounter(input) {
            const field = input.closest('.wbzp-intake-field');
            const counter = field && field.querySelector('.wbzp-intake-char-counter');
            if (!counter) return;
            const max = input.maxLength;
            const used = input.value.length;
            const nearLimit = used >= max * 0.9 && used < max;
            const atLimit = used >= max;
            counter.textContent = nearLimit || atLimit ? `${used} / ${max}` : '';
            counter.classList.toggle('is-near-limit', nearLimit);
            counter.classList.toggle('is-at-limit', atLimit);
        }

        form.querySelectorAll('input[maxlength], textarea[maxlength]').forEach(updateCounter);
        form.addEventListener('input', (event) => {
            if (event.target.maxLength >= 0) updateCounter(event.target);
        });

        const touchedFields = new WeakSet();

        function showFieldValidity(field) {
            const invalidInputs = field.querySelectorAll('input:invalid, select:invalid, textarea:invalid');
            const ownInvalidInput = Array.from(invalidInputs).some((input) => {
                const companion = input.closest('.wbzp-intake-field-companion');
                return !companion || !field.contains(companion);
            });
            field.classList.toggle('has-error', ownInvalidInput);
        }

        let state = 'idle';

        function updateSubmitAvailability() {
            if (state !== 'idle') return;
            submitButton.disabled = !form.checkValidity();
        }

        function setControlsDisabled(disabled) {
            form.querySelectorAll('input, select, textarea').forEach((control) => {
                control.disabled = disabled;
            });
        }

        form.addEventListener('focusout', (event) => {
            const field = event.target.closest('.wbzp-intake-field');
            if (field) {
                touchedFields.add(field);
                showFieldValidity(field);
            }
            updateSubmitAvailability();
        });
        form.addEventListener('change', (event) => {
            const field = event.target.closest('.wbzp-intake-field');
            if (field) {
                touchedFields.add(field);
                showFieldValidity(field);
            }
            updateSubmitAvailability();
        });
        form.addEventListener('input', (event) => {
            const field = event.target.closest('.wbzp-intake-field');
            if (field && touchedFields.has(field)) showFieldValidity(field);
            updateSubmitAvailability();
        });
        updateSubmitAvailability();

        function showResult(ok, data) {
            message.hidden = false;
            form.classList.remove('is-submitting');
            form.removeAttribute('aria-busy');
            if (ok && data && data.success) {
                state = 'submitted';
                message.textContent = 'Your submission has been received.';
                message.className = 'wbzp-intake-message is-success';
                submitButton.textContent = 'Submitted';
                return;
            }
            state = 'idle';
            setControlsDisabled(false);
            message.textContent = (data && data.error) || 'Something went wrong. Please try again.';
            message.className = 'wbzp-intake-message is-error';
            submitButton.textContent = submitButtonText;
            updateSubmitAvailability();
            if (window.turnstile) {
                window.turnstile.reset();
            }
        }

        form.addEventListener('submit', (event) => {
            event.preventDefault();

            if (state !== 'idle') return;

            if (!form.checkValidity()) {
                form.reportValidity();
                return;
            }

            const body = new FormData(form);
            state = 'submitting';
            form.classList.add('is-submitting');
            form.setAttribute('aria-busy', 'true');
            setControlsDisabled(true);
            submitButton.disabled = true;
            submitButton.textContent = 'Submitting…';
            message.hidden = true;

            fetch(form.action, { method: 'POST', body })
                .then((response) => {
                    return response.json().then((data) => {
                        showResult(response.ok, data);
                    });
                })
                .catch(() => {
                    showResult(false, null);
                });
        });
    }

    document.querySelectorAll('.wbzp-intake-form').forEach(initForm);
})();
