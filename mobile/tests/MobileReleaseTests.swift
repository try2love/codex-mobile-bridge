import Foundation
@main struct MobileReleaseTests {
 static func main() throws {
  func check(_ value: Bool) { precondition(value) }
  func row(_ version: String, draft: Bool = false, prerelease: Bool = true, trusted: Bool = true, asset: Bool = true) -> [String: Any] {
   let name = "Codex-Mobile-Bridge-\(version)-iOS-source.zip"
   return ["tag_name":"v"+version,"draft":draft,"prerelease":prerelease,"assets": asset ? [["name":name,"browser_download_url":trusted ? MobileRelease.repository+"/releases/download/v"+version+"/"+name : "https://evil.example/source.zip"]] : []]
  }
  func select(_ rows: [[String: Any]], _ current: String = "2.0.0-preview.1") throws -> String? {
   try MobileRelease.select(JSONSerialization.data(withJSONObject: rows),current:current)?.version
  }
  let rows = [row("2.0.0-preview.2"),row("2.0.0-preview.10"),row("3.0.0",draft:true)]
  check(try select(rows) == "2.0.0-preview.10")
  check(try select(rows,"1.4.0") == nil)
  for r in [row("2.0.0-preview.2",prerelease:false),row("2.0.0",trusted:false),row("2.0.0",asset:false),row("2.0.0-preview.02")] { check(try select([r],"1.4.0") == nil) }
  check(try select([row("2.0.0",prerelease:false)]) == "2.0.0")
  check(try select([row("2.0.0-preview.1")]) == nil)
  precondition(!MobileRelease.newer("2.0.0-preview.999999999999999999999999",than:"2.0.0"))
  print("Swift: mobile release channel, version and asset checks passed")
 }
}
