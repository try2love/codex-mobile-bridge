/* Run in the gateway browser alongside tests/markdown.test.js. */
(() => {
  const checks = [];
  function check(name, condition) {
    if (!condition) throw Error(name);
    checks.push(name);
  }
  function render(text) {
    const node = document.createElement('div');
    node.className = 'message assistant';
    renderMarkdown(node, text);
    return node;
  }
  const formulas = String.raw`视图 \(V\)，答案 \(p_v(A)\)。

\[
U(q)=H\!\left(\sum_v w_v p_v(A)\right)
-\sum_v w_vH\!\left(p_v(A)\right)
\]

\[
\max I(Z;Y\mid Q)-\beta I(Z;D\mid Q)
\]

行内 $x^2$ 与

$$\frac{a}{b}$$`;
  const node = render(formulas);
  check('bracket and dollar math render', node.querySelectorAll('.katex').length === 6);
  check('display and inline modes', node.querySelectorAll('.katex-display').length === 3);
  check('TeX survives Markdown escapes', node.querySelectorAll('annotation')[2]?.textContent.includes(String.raw`\sum_v w_v p_v(A)`));
  check('fractions have MathML', node.querySelector('mfrac'));
  check('code and escaped dollars stay literal', !render('`\\(x\\)`\n\n```tex\n\\[x^2\\]\n```\n\n\\$x\\$').querySelector('.katex'));
  check('currency stays text', !render('价格 $20 和 $30，合计 $50。').querySelector('.katex'));
  check('plain brackets stay text', render('[这是说明]').textContent === '[这是说明]');
  const unsafe = render(String.raw`\(\href{javascript:alert(1)}{click}\) \(\includegraphics{https://example.com/track.png}\) \(\htmlStyle{background:url(https://example.com)}{x}\)`);
  check('TeX cannot create links, images or user styles', !unsafe.querySelector('a,img,[style*="example.com"]'));
  const broken = render(String.raw`\[\frac{a}\]`);
  check('invalid formula remains readable', broken.textContent.includes(String.raw`\frac{a}`));
  for (let i = 1; i <= formulas.length; i += 7) render(formulas.slice(0, i));
  check('partial streaming formulas do not throw', true);
  const fixture = document.createElement('div');
  fixture.style.width = '300px';
  fixture.append(render('\\[' + 'a+b+'.repeat(70) + 'c\\]'));
  document.body.append(fixture);
  const display = fixture.querySelector('.math-display');
  const fits = fixture.scrollWidth <= 301 && display && display.scrollWidth > display.clientWidth;
  fixture.remove();
  check('long equations scroll within the phone width', fits);
  return {passed:checks.length, checks};
})();
