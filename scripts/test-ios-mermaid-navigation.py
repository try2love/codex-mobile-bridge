#!/usr/bin/env python3
"""Compile the iOS shell URL predicate and check its navigation policy uses."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.tmp/ios-mermaid-navigation-tests'
WORK.mkdir(parents=True, exist_ok=True)
source = (ROOT / 'mobile/ios/BridgePreview/App.swift').read_text()

policy = source[source.index('    func webView(_ webView: WKWebView, decidePolicyFor action:'):
                source.index('    func webView(_ webView: WKWebView, didFinish navigation:')]
subframe = policy[policy.index('        if action.targetFrame?.isMainFrame == false {'):
                  policy.index('        if action.targetFrame?.isMainFrame == true,')]
assert 'decisionHandler(isIsolatedShell(url) ? .allow : .cancel); return' in subframe
assert 'if isIsolatedShell(url) { decisionHandler(.cancel); return }' in policy[len(subframe):]

start = source.index('    private func isIsolatedShell(_ url: URL) -> Bool {')
end = source.index('    private func downloadArtifact(', start)
method = source[start:end]
fixture = '''import Foundation
final class Controller {
    let documentAddress: String
    init(_ origin: String) { self.documentAddress = origin }
''' + method + '''
    func accepts(_ address: String) -> Bool { isIsolatedShell(URL(string: address)!) }
}
func check(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() { fatalError(message) }
}
let origins = ["https://bridge.example", "https://relay.example/d/0123456789abcdef0123456789abcdef"]
for origin in origins {
    let controller = Controller(origin)
    for path in ["/html-preview-shell", "/mermaid-render-shell", "/visualize-shell"] {
        check(controller.accepts(origin + path), "exact shell should be allowed: \\(origin + path)")
        for suffix in ["-other", "/extra", "?x=1", "#fragment"] {
            check(!controller.accepts(origin + path + suffix), "shell suffix should be rejected: \\(path + suffix)")
        }
        check(!controller.accepts("https://elsewhere.example" + path), "external origin should be rejected")
        check(!controller.accepts("http://bridge.example" + path), "different scheme should be rejected")
    }
}
let relay = Controller(origins[1])
check(!relay.accepts(origins[0] + "/mermaid-render-shell"), "relay shell must remain device scoped")
check(!relay.accepts(origins[0] + "/visualize-shell"), "visualize shell must remain device scoped")
print("iOS isolated shell navigation passed")
'''
(WORK / 'main.swift').write_text(fixture)
subprocess.run(['xcrun', 'swiftc', '-module-cache-path', str(WORK / 'cache'),
                str(WORK / 'main.swift'), '-o', str(WORK / 'mermaid-navigation')], check=True)
subprocess.run([str(WORK / 'mermaid-navigation')], check=True)
