document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.flash-close').forEach((button) => {
    button.addEventListener('click', () => button.closest('.flash')?.remove());
  });

  document.querySelectorAll('[data-toggle-password]').forEach((button) => {
    button.addEventListener('click', () => {
      const input = document.getElementById(button.dataset.togglePassword);
      if (!input) return;
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      button.textContent = show ? 'Hide' : 'Show';
      button.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
    });
  });

  document.querySelectorAll('[data-copy]').forEach((button) => {
    button.addEventListener('click', async () => {
      const value = button.dataset.copy;
      try {
        await navigator.clipboard.writeText(value);
        const previous = button.textContent;
        button.textContent = 'Copied';
        window.setTimeout(() => { button.textContent = previous; }, 1000);
      } catch (_error) {
        // Clipboard access may be disabled on an unsecured origin; users can select the displayed credentials.
        button.textContent = value;
      }
    });
  });

  const fileInput = document.getElementById('file');
  if (fileInput) {
    fileInput.addEventListener('change', () => {
      const label = document.getElementById('file-name');
      if (label) label.textContent = fileInput.files?.[0]?.name || 'Choose a CSV file';
    });
  }

  const departmentSelect = document.getElementById('assign-department');
  const officerSelect = document.getElementById('assigned_to');
  if (departmentSelect && officerSelect) {
    const filterOfficers = () => {
      const departmentId = departmentSelect.value;
      Array.from(officerSelect.options).forEach((option) => {
        if (!option.value) return;
        const match = !departmentId || option.dataset.department === departmentId;
        option.hidden = !match;
        option.disabled = !match;
        if (!match && option.selected) officerSelect.value = '';
      });
    };
    departmentSelect.addEventListener('change', filterOfficers);
    filterOfficers();
  }
});
