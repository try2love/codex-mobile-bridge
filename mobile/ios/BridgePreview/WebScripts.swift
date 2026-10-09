import Foundation

// Resource loading only; frame, origin and token validation stay with the native bridge.
struct WebScripts {
    let appearance: String
    let clipboard: String

    init(bundle: Bundle = .main) throws {
        appearance = try Self.source("mobile-ui", bundle: bundle)
        clipboard = try Self.source("mobile-clipboard", bundle: bundle)
    }

    private static func source(_ name: String, bundle: Bundle) throws -> String {
        guard let url = bundle.url(forResource: name, withExtension: "js") else {
            throw NSError(domain: NSCocoaErrorDomain, code: NSFileReadNoSuchFileError,
                          userInfo: [NSFilePathErrorKey: name + ".js"])
        }
        return try String(contentsOf: url, encoding: .utf8)
    }

    // Mobile-only presentation; ordinary browser pages keep their own scaling.
    static let fixedViewport = """
    (() => {
      const viewport = document.querySelector('meta[name="viewport"]');
      if (viewport) viewport.content = 'width=device-width,initial-scale=1,minimum-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover';
    })();
    """
}
