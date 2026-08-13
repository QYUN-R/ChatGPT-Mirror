(function recoverGatewayControls() {
  var failureReported = false;
  var recoveryUrl = '/admin/#/login-chatgpt?upstream=expired';
  var readCsrfToken = function() {
    var match = document.cookie.match(/(?:^|; )csrftoken=([^;]*)/);
    if (!match) return '';
    try {
      return decodeURIComponent(match[1]);
    } catch (_error) {
      return match[1];
    }
  };
  var reportSessionFailure = function() {
    if (failureReported) return;
    failureReported = true;
    var payload = {
      chatgpt_id: sessionStorage.getItem('tuwugpt.activePoolAccountId') || null,
      error_code: 'session_expired'
    };
    var headers = { 'Content-Type': 'application/json' };
    var csrfToken = readCsrfToken();
    if (csrfToken) headers['X-CSRFToken'] = csrfToken;
    fetch('/0x/chatgpt/session-failure', {
      method: 'POST',
      credentials: 'same-origin',
      headers: headers,
      body: JSON.stringify(payload),
      keepalive: true
    }).catch(function() {});
  };
  var hasExpiredDialog = function() {
    var dialogs = document.querySelectorAll('[role="dialog"], [aria-modal="true"]');
    for (var i = 0; i < dialogs.length; i += 1) {
      var text = (dialogs[i].textContent || '').replace(/\s+/g, ' ');
      if (/会话已过期|session has expired|session expired/i.test(text)) return true;
    }
    return false;
  };
  var makeInteractive = function(element) {
    if (!element) return;
    element.removeAttribute('inert');
    element.setAttribute('aria-hidden', 'false');
    element.style.setProperty('pointer-events', 'auto', 'important');
  };
  var isRecoveryControl = function(element) {
    if (!element) return false;
    if (element.id === 'tuwugpt-session-recovery-control') return true;
    return Boolean(
      element.closest('#gateway-user-logout-anchor')
      && (element.textContent || '').trim() === '返回后台 / 换号'
    );
  };
  var redirectToAccountPicker = function(event) {
    if (event) {
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
    }
    reportSessionFailure();
    window.location.replace(recoveryUrl);
  };
  var ensureFallbackControl = function() {
    var fallback = document.getElementById('tuwugpt-session-recovery-control');
    if (!hasExpiredDialog()) {
      if (fallback) fallback.remove();
      return;
    }
    var gatewayButton = Array.from(document.querySelectorAll('#gateway-user-logout-anchor button')).find(function(item) {
      return (item.textContent || '').trim() === '返回后台 / 换号';
    });
    if (gatewayButton) {
      if (fallback) fallback.remove();
      return;
    }
    if (!fallback) {
      fallback = document.createElement('button');
      fallback.id = 'tuwugpt-session-recovery-control';
      fallback.type = 'button';
      fallback.textContent = '返回后台 / 换号';
      fallback.setAttribute('aria-label', '返回后台 / 换号');
      fallback.style.setProperty('position', 'fixed', 'important');
      fallback.style.setProperty('top', 'max(12px, env(safe-area-inset-top))', 'important');
      fallback.style.setProperty('right', '12px', 'important');
      fallback.style.setProperty('z-index', '2147483647', 'important');
      fallback.style.setProperty('padding', '10px 14px', 'important');
      fallback.style.setProperty('border', '1px solid rgba(255,255,255,.16)', 'important');
      fallback.style.setProperty('border-radius', '10px', 'important');
      fallback.style.setProperty('background', '#111827', 'important');
      fallback.style.setProperty('color', '#fff', 'important');
      fallback.style.setProperty('font', '500 13px/1.2 system-ui, sans-serif', 'important');
      fallback.style.setProperty('box-shadow', '0 8px 24px rgba(0,0,0,.24)', 'important');
      fallback.style.setProperty('cursor', 'pointer', 'important');
      (document.body || document.documentElement).appendChild(fallback);
    }
    makeInteractive(fallback);
  };
  var bind = function() {
    if (hasExpiredDialog()) reportSessionFailure();
    var anchor = document.getElementById('gateway-user-logout-anchor');
    if (anchor) {
      makeInteractive(anchor);
      var interactive = anchor.querySelectorAll(
        '#gateway-user-logout-shell, #gateway-user-logout-actions, button, a, #gateway-user-logout-handle'
      );
      for (var i = 0; i < interactive.length; i += 1) {
        makeInteractive(interactive[i]);
      }

      var button = Array.from(anchor.querySelectorAll('button')).find(function(item) {
        return (item.textContent || '').trim() === '返回后台 / 换号';
      });
      if (button) button.setAttribute('data-session-recovery-bound', 'true');
    }
    ensureFallbackControl();
    return Boolean(anchor);
  };
  document.addEventListener('click', function(event) {
    var target = event.target instanceof Element ? event.target.closest('button, a') : null;
    if (isRecoveryControl(target)) redirectToAccountPicker(event);
  }, true);
  bind();
  var observer = new MutationObserver(bind);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  window.setInterval(bind, 750);
})();
