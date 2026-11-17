// Display only structured control receipts. Assistant prose cannot confirm an action.
export function mountCommandFeedback() {
  const stage = document.querySelector('canvas').parentElement;
  const notice = document.createElement('div');
  notice.className = 'iris-command-notice';
  notice.setAttribute('role', 'status');
  notice.setAttribute('aria-live', 'polite');
  notice.setAttribute('aria-atomic', 'true');
  const title = document.createElement('strong');
  const message = document.createElement('span');
  notice.append(title, message);
  notice.hidden = true;
  const badge = document.createElement('span');
  badge.className = 'iris-companion-badge';
  badge.textContent = 'Companion on';
  badge.hidden = true;
  stage.append(notice, badge);
  let timer;
  const clear = () => { clearTimeout(timer); notice.hidden = true; };
  window.addEventListener('pagehide', clear);
  return {
    clear,
    companion(enabled) { badge.hidden = enabled !== true; },
    show(result, milliseconds = 6000) {
      clear();
      if (!result || typeof result.title !== 'string' || typeof result.message !== 'string' ||
          !['success', 'error', 'info', 'processing'].includes(result.status)) return;
      title.textContent = result.title.slice(0, 80);
      message.textContent = result.message.slice(0, 320);
      notice.dataset.status = result.status;
      notice.hidden = false;
      timer = setTimeout(clear, milliseconds);
    },
  };
}
