(() => {
  if (document.documentElement.classList.contains('bridge-mobile')) return;
  const sheet = Array.from(document.styleSheets).find(s => s.href && new URL(s.href).origin === location.origin);
  const app = document.getElementById('app');
  if (!sheet || !app) return;
  // Reuse a same-origin sheet: the gateway intentionally disallows inline styles.
  for (const rule of [
    '.bridge-mobile .phone-header { min-height: 56px; padding: 4px 64px 4px 16px; }',
    '.bridge-mobile.bridge-authenticated .phone-header { display: none !important; }',
    '.bridge-mobile aside { padding-top: 0; }',
    '.bridge-mobile .list-heading { min-height: 56px; padding-right: 48px; }',
    '.bridge-mobile .list-heading button { height: 48px; display: inline-flex; align-items: center; justify-content: center; margin: 0; }',
    '.bridge-mobile .chat-head { min-height: 56px; padding: 4px 64px 4px 8px; gap: 6px; }',
    '.bridge-mobile .chat-head .appearance-button { display: none; }',
    '.bridge-mobile #back { width: 40px; height: 48px; padding: 8px; margin-left: 0; flex: none; }',
    '.bridge-mobile .sidebar-foot { order: 99; flex: none; min-height: 44px; padding: 2px 16px max(4px, env(safe-area-inset-bottom)); background: var(--page); }',
    '.bridge-mobile:not(.bridge-authenticated) .sidebar-foot { display: none; }',
    '.bridge-mobile body.chat-detail > .sidebar-foot { display: none; }',
    '.bridge-mobile body.chat-detail .composer { padding-bottom: max(8px, env(safe-area-inset-bottom)); }',
    '.bridge-mobile .sidebar-foot #logout { display: none; }',
    '.bridge-mobile .sidebar-foot button, .bridge-mobile .sidebar-foot a { min-height: 40px; display: inline-flex; align-items: center; text-decoration: none; }',
    '.bridge-mobile .sidebar-foot > span { display: none; }',
    '.bridge-mobile #appearance-dialog #pushplus-settings { min-height: 44px; }',
    '.bridge-mobile .composer { padding-bottom: 8px; }',
    '.bridge-mobile input:not([type=checkbox]):not([type=radio]), .bridge-mobile textarea { font-size: max(16px, 1em); }'
  ]) sheet.insertRule(rule, sheet.cssRules.length);
  document.documentElement.classList.add('bridge-mobile');
  const sync = () => document.documentElement.classList.toggle('bridge-authenticated', !app.hidden);
  sync();
  new MutationObserver(sync).observe(app, { attributes: true, attributeFilter: ['hidden'] });
  const account = document.getElementById('accounts-button');
  if (account) {
    const labelAccount = () => {
      if (account.getAttribute('data-i18n') === '账号与额度') {
        const label = document.documentElement.lang.startsWith('en') ? 'Official account' : '官方账号';
        if (account.textContent !== label) account.textContent = label;
      }
    };
    new MutationObserver(labelAccount).observe(account, { attributes: true, attributeFilter: ['data-i18n'], childList: true });
    labelAccount();
  }
  const footer = document.querySelector('.sidebar-foot');
  if (footer) {
    document.body.appendChild(footer);
    const home = document.createElement('a'); home.href = 'codexbridge://home'; home.className = 'plain'; home.dataset.i18n = '返回电脑列表'; home.textContent = typeof BridgeI18n !== 'undefined' ? BridgeI18n.t('返回电脑列表') : '返回电脑列表';
    footer.appendChild(home);
  }
  const push = document.getElementById('pushplus-settings'), settings = document.getElementById('appearance-dialog');
  if (push && settings) {
    settings.insertBefore(push, settings.querySelector('.appearance-actions'));
    push.addEventListener('click', () => settings.close());
  }
})();
