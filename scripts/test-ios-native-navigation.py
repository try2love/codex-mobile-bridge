#!/usr/bin/env python3
"""Compile the production iOS native-navigation guard with synthetic WebKit actions."""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.tmp/ios-native-navigation-tests'
WORK.mkdir(parents=True, exist_ok=True)
source = (ROOT / 'mobile/ios/BridgePreview/App.swift').read_text()
declaration = re.search(r'^    private var nativeMainNavigation: .*$', source, re.MULTILINE).group(0)
start = source.index('        if action.targetFrame?.isMainFrame == true, nativeMainNavigation?.web === webView {')
end = source.index('        if isIsolatedShell(url)', start)
guard = source[start:end]
typed = 'navigationType: WKNavigationType' in declaration
token = '(web, generation, url.absoluteString, type)' if typed else '(web, generation, url.absoluteString)'
fixture = '''import Foundation
enum WKNavigationType: Int { case other = -1, linkActivated = 0, formSubmitted = 1, backForward = 2, reload = 3 }
enum WKNavigationActionPolicy { case allow, cancel }
final class WKWebView {}
struct Frame { let isMainFrame: Bool }
struct Request { let url: URL? }
struct WKNavigationAction {
    let request: Request
    let navigationType: WKNavigationType
    let sourceFrame: Frame
    let targetFrame: Frame?
}
final class Controller {
    var generation = 7
    let documentAddress = "https://gateway.example/d/0123456789abcdef0123456789abcdef"
''' + declaration.replace('private ', '', 1) + '''
    func arm(_ web: WKWebView, _ url: URL, type: WKNavigationType) {
        nativeMainNavigation = TOKEN
    }
    func webView(_ webView: WKWebView, action: WKNavigationAction, decisionHandler: (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
GUARD
        decisionHandler(.allow)
    }
}
let controller = Controller(), web = WKWebView(), otherWeb = WKWebView()
let url = URL(string: controller.documentAddress + "/?client=codex")!
func check(_ expected: WKNavigationActionPolicy, _ name: String, armed: Bool = true,
           actualType: WKNavigationType = .reload, expectedType: WKNavigationType = .reload,
           sourceMain: Bool = false, targetMain: Bool? = true, useOtherWeb: Bool = false,
           stale: Bool = false, actualURL: URL? = nil) {
    controller.nativeMainNavigation = nil
    if armed { controller.arm(web, url, type: expectedType) }
    if stale { controller.generation += 1 }
    let action = WKNavigationAction(request: Request(url: actualURL ?? url), navigationType: actualType,
                                   sourceFrame: Frame(isMainFrame: sourceMain), targetFrame: targetMain.map(Frame.init))
    var result: WKNavigationActionPolicy?
    controller.webView(useOtherWeb ? otherWeb : web, action: action) { result = $0 }
    precondition(result == expected, name)
    if !useOtherWeb && targetMain == true && armed { precondition(controller.nativeMainNavigation == nil, "Token is single-use") }
    print(name + " passed")
}
check(.allow, "exact native reload")
check(.allow, "exact native load", actualType: .other, expectedType: .other)
check(.cancel, "reload cannot consume load token", actualType: .reload, expectedType: .other)
check(.cancel, "load cannot consume reload token", actualType: .other, expectedType: .reload)
check(.cancel, "link cannot consume native token", actualType: .linkActivated)
check(.cancel, "unarmed non-main source", armed: false)
check(.cancel, "different web view", useOtherWeb: true)
check(.cancel, "stale generation", stale: true)
check(.cancel, "different URL", actualURL: URL(string: controller.documentAddress + "/?client=claude"))
check(.cancel, "different gateway", actualURL: URL(string: "https://elsewhere.example/"))
check(.cancel, "different relay device", actualURL: URL(string: "https://gateway.example/d/ffffffffffffffffffffffffffffffff/"))
check(.cancel, "missing target frame", targetMain: nil)
check(.allow, "main-frame page navigation", armed: false, actualType: .linkActivated, sourceMain: true)
'''.replace('TOKEN', token).replace('GUARD', guard)
if typed:
    native_load = source[source.index('    private func loadNative('):source.index('    private func ', source.index('    private func loadNative(') + 10)]
    native_refresh = source[source.index('    @objc private func refresh()'):source.index('    func webView(', source.index('    @objc private func refresh()'))]
    assert '(web, generation, $0.absoluteString, .other)' in native_load
    assert '(web, generation, $0.absoluteString, .reload)' in native_refresh
(WORK / 'main.swift').write_text(fixture)
subprocess.run(['xcrun', 'swiftc', '-module-cache-path', str(WORK / 'cache'),
                str(ROOT / 'mobile/ios/BridgePreview/GatewayURL.swift'), str(WORK / 'main.swift'),
                '-o', str(WORK / 'native-navigation')], check=True)
subprocess.run([str(WORK / 'native-navigation')], check=True)
