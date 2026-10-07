(() => {
  if (window !== window.top) return;
  const nativePrompt = window.prompt.bind(window);
  const command = 'codexbridge-copy:__BRIDGE_CLIPBOARD_TOKEN__';
      const syncLanguage = () => { if (typeof BridgeI18n !== 'undefined') nativePrompt('codexbridge-language:__BRIDGE_CLIPBOARD_TOKEN__', BridgeI18n.language()); };
      document.addEventListener('bridge-language', syncLanguage); syncLanguage();
  let clicked = false;
  document.addEventListener('click', event => {
    clicked = event.isTrusted && !!event.target.closest?.('.message-copy, .code-copy');
  }, true);
  const install = () => {
    if (!window.BridgeClipboard) return;
    window.BridgeClipboard.copy = async (getText, button) => {
      if (button.disabled || !clicked) return;
      clicked = false;
      const translate = text => typeof BridgeI18n === 'undefined' ? text : BridgeI18n.t(text);
      const label = button.textContent;
      button.disabled = true;
      button.textContent = translate('正在读取…');
      try {
        const text = await getText();
        if (typeof text !== 'string') throw new Error('无法读取复制内容');
        if (text.length > 262144) throw new Error('内容过长，请分段复制');
        if (nativePrompt(command, text) !== 'copied') throw new Error('复制失败，请重试');
        button.textContent = translate('已复制');
        setTimeout(() => { button.textContent = label; }, 1500);
      } catch (error) {
        button.textContent = label;
        document.dispatchEvent(new CustomEvent('bridge-message-error', { detail: error.message }));
      } finally {
        button.disabled = false;
      }
    };
  };
  install();
  document.addEventListener('DOMContentLoaded', install, { once: true });
})();
