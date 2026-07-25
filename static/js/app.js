document.querySelectorAll('[data-question]').forEach((button) => button.addEventListener('click', () => {
  const input = document.querySelector('textarea[name="question"]');
  if (input) { input.value = button.dataset.question; input.focus(); }
}));
document.querySelectorAll('[data-loading-form]').forEach((form) => form.addEventListener('submit', () => {
  form.classList.add('is-loading');
  form.querySelectorAll('button[type="submit"], button:not([type])').forEach((button) => button.disabled = true);
}));
