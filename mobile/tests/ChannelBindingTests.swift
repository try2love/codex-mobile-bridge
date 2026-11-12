// Production handoff methods are injected by test-ios-channel-binding.py.
import Foundation
import UIKit
@preconcurrency import WebKit

private enum DeviceBinding {
    struct Credential {let name:String,token:String;var cookie:String{name+"="+token}}
    static var values=[String:Credential]()
    static func load(_ key:String)->Credential?{values[key]}
    @discardableResult static func save(_ key:String,_ value:Credential)->Bool{values[key]=value;return true}
}
private final class MemoryDefaults {
    var values=[String:Any]()
    func string(forKey key:String)->String?{values[key] as? String}
    func object(forKey key:String)->Any?{values[key]}
    func set(_ value:Any,forKey key:String){values[key]=value}
}
@MainActor private final class CookieStore {
    let data=WebKit.WKWebsiteDataStore.nonPersistent()
    var reads=0,writes=0,deletes=0
    var afterRead:(() async->Void)?,afterWrite:(() async->Void)?
    func allCookies() async->[HTTPCookie]{
        let result=await data.httpCookieStore.allCookies();reads+=1
        let hook=afterRead;afterRead=nil;await hook?();return result
    }
    func setCookie(_ cookie:HTTPCookie,completionHandler:@escaping @MainActor ()->Void){
        writes+=1
        data.httpCookieStore.setCookie(cookie){[self] in
            let hook=afterWrite;afterWrite=nil
            Task{@MainActor in await hook?();completionHandler()}
        }
    }
    func delete(_ cookie:HTTPCookie,completionHandler:@escaping @MainActor ()->Void){
        deletes+=1;data.httpCookieStore.delete(cookie,completionHandler:completionHandler)
    }
    func seed(_ address:String,_ token:String,secure:Bool?=nil,httpOnly:Bool=true) async{
        var fields:[HTTPCookiePropertyKey:Any]=[.name:Fixture.name,.value:token,.domain:URL(string:address)!.host!,.path:"/"]
        if secure ?? address.hasPrefix("https://"){fields[.secure]="TRUE"}
        if httpOnly{fields[HTTPCookiePropertyKey("HttpOnly")]="TRUE"}
        await data.httpCookieStore.setCookie(HTTPCookie(properties:fields)!)
    }
    func value(_ address:String) async->String?{
        await data.httpCookieStore.allCookies().first{cookie in
            cookie.name==Fixture.name && cookie.path=="/" && cookie.domain.trimmingCharacters(in:CharacterSet(charactersIn:"."))==URL(string:address)!.host &&
            (!cookie.isSecure || address.hasPrefix("https://"))
        }?.value
    }
}
@MainActor private enum WKWebsiteDataStore {
    static var store=CookieStore()
    static func `default`()->WKWebsiteDataStoreBox{WKWebsiteDataStoreBox()}
}
@MainActor private struct WKWebsiteDataStoreBox {var httpCookieStore:CookieStore{WKWebsiteDataStore.store}}
private enum MobileStrings {static func text(_ value:String)->String{value}}
private struct Failure:Error {let message:String}
private func check(_ result:Bool,_ message:String)throws{if !result{throw Failure(message:message)}}

@MainActor private final class Fixture:NSObject,WKUIDelegate,WKNavigationDelegate {
    static let name="codex_mobile_session_"+String(repeating:"1",count:24),id=String(repeating:"a",count:32),root=String(repeating:"r",count:64),newRoot=String(repeating:"n",count:64)
    var origin:String,documentAddress:String,active=true,pageReady=true,generation=1,channelTicket=1,bindingRevision=1
    var channelCookieOwners=[String:UUID]()
    let defaults=MemoryDefaults(),cookies=CookieStore()
    var foregroundBaselines=Set<String>(),arrivingVia="",connected=[String](),notices=[String]()
    private var pendingPeer:RoutePeer?
    var peerPrepared:Bool{pendingPeer != nil}
    var web:WKWebView!,navigationReady=false,promptReplies=[String](),promptStarted=false
    var duringPrompt:(() async->Void)?,onCommit:(()->Void)?
    var hidden:WKWebView?,targetLoaded:CallbackGate?
    var connectionError:UIView?,connectionDeadline:DispatchWorkItem?
    let subtitle=UILabel()
    func hideEntryProgress(){}
    func syncWebTheme(){}
    var saved:[String]{defaults.object(forKey:"origins") as? [String] ?? []}
    init(_ source:String,_ target:String,foreign:Bool=false){
        origin=source;documentAddress=source
        super.init()
        WKWebsiteDataStore.store=cookies;DeviceBinding.values=[:]
        defaults.set([source],forKey:"origins");defaults.set(Self.name,forKey:"cookieName:"+source)
        defaults.set(source,forKey:"channel-anchor:"+Self.id)
        let descriptor:[String:Any]=["deviceId":Self.id,"anchor":source,"endpoints":[source,target]]
        defaults.set(descriptor,forKey:"channels:"+source)
        if foreign{defaults.set(["deviceId":String(repeating:"b",count:32)],forKey:"channels:"+target)}
        DeviceBinding.save(GatewayChannels.vault(Self.id),.init(name:Self.name,token:Self.root))
        DeviceBinding.save(source,.init(name:Self.name,token:Self.root))
        let config=WKWebViewConfiguration();config.websiteDataStore=cookies.data
        web=WKWebView(frame:CGRect(x:0,y:0,width:300,height:300),configuration:config)
        web.uiDelegate=self;web.navigationDelegate=self
    }
    func channelRecord(_ address:String)->[String:Any]?{defaults.object(forKey:"channels:"+address) as? [String:Any]}
    func addresses(_ address:String)->[String]{channelRecord(address).map(GatewayChannels.endpoints) ?? []}
    func connect(_ url:URL,address:String){connected.append(address)}
    func routeNotice(_ text:String){notices.append(text)}
    func begin(_ next:String,opening:Bool=false) async{
        let source=origin,screen=generation,page=web
        let operation=moveChannel(source,channelRecord(source)!,to:next,requested:URL(string:source+"/#draft")!,view:opening ? nil:page,failure:false,
                    current:{self.active && self.generation==screen && self.web===page && self.origin==source})
        await operation?.value
    }
    func finish() async->Bool{guard let peer=pendingPeer else{return false};pendingPeer=nil;return await peer.commit({true})}
    func takeRollback()->(@MainActor () async->Void){let peer=pendingPeer!;pendingPeer=nil;return peer.rollback}
    func fail() async{guard let peer=pendingPeer else{return};pendingPeer=nil;await peer.rollback()}
    func webView(_ webView:WKWebView,didFinish navigation:WKNavigation!){
        if webView===web{navigationReady=true;onCommit?()}else{targetLoaded?.hit()}
    }
    func prepareHiddenTarget(_ address:String) async throws{
        let config=WKWebViewConfiguration();config.websiteDataStore=cookies.data
        hidden=WKWebView(frame:CGRect(x:0,y:0,width:1,height:1),configuration:config);hidden?.navigationDelegate=self
        let loaded=CallbackGate();targetLoaded=loaded
        hidden?.loadHTMLString("<p>Isolated target peer</p>",baseURL:URL(string:address+"/mobile-channel"))
        guard await loaded.wait() else{throw Failure(message:"hidden target did not load")}
    }
    func webView(_ webView:WKWebView,runJavaScriptTextInputPanelWithPrompt prompt:String,defaultText:String?,initiatedByFrame frame:WKFrameInfo,completionHandler:@escaping (String?)->Void){
        guard prompt=="fixture-ready",web===webView,frame.isMainFrame,let peer=pendingPeer,defaultText==peer.nonce else{completionHandler(nil);return}
        promptStarted=true
        let screen=generation,page=web,originalURL=webView.url
        if let hook=duringPrompt{Task{@MainActor in await hook()}}
        commitPeer(peer,eligible:{self.active && self.generation==screen && self.web===page && webView.url==originalURL && !webView.isLoading},reply:{answer in self.promptReplies.append(answer);completionHandler(answer)})
    }
    func loadDocument() async throws {
        navigationReady=false
        UIApplication.shared.connectedScenes.compactMap{$0 as? UIWindowScene}.flatMap{$0.windows}.first(where:{$0.isKeyWindow})?.rootViewController?.view.addSubview(web)
        let loaded=CallbackGate();onCommit={loaded.hit()}
        web.loadHTMLString("<html><body>Isolated native CookieStore test</body></html>",baseURL:URL(string:origin+"/"))
        guard await loaded.wait() else{throw Failure(message:"native test document did not load")}
        onCommit=nil
    }
    func prompt() async throws->String{
        guard navigationReady,let nonce=pendingPeer?.nonce else{throw Failure(message:"source document or handoff missing")}
        return try await withCheckedThrowingContinuation{continuation in
            web.evaluateJavaScript("prompt('fixture-ready','"+nonce+"')"){value,error in
                if let error{continuation.resume(throwing:error)}else{continuation.resume(returning:value as? String ?? "nil")}
            }
        }
    }
    // PRODUCTION_MAIN_COMMIT
    // PRODUCTION_COOKIE_SELECTION
    // PRODUCTION_GATEWAY_IDENTITY
    // PRODUCTION_MOVE_CHANNEL_AND_PEER
}

@MainActor private final class CallbackGate {
    var ready=false,continuation:CheckedContinuation<Bool,Never>?
    func hit(){ready=true;continuation?.resume(returning:true);continuation=nil}
    func wait(_ seconds:Double=4) async->Bool{
        if ready{return true}
        return await withCheckedContinuation{reply in
            continuation=reply
            DispatchQueue.main.asyncAfter(deadline:.now()+seconds){[self] in
                continuation?.resume(returning:false);continuation=nil
            }
        }
    }
}
@MainActor private enum Suite {
    static var passed=[String](),details=[String:Any]()
    static func run() async throws{
        let source="https://10.248.248.248:8788",same="https://10.248.248.248:8790",http="http://10.248.248.248:8790",other="https://other-fixture.invalid"
        if ProcessInfo.processInfo.environment["CHANNEL_BINDING_FOCUS"]=="ownership" {try await ownershipCases(source,same);return}
        if ProcessInfo.processInfo.environment["CHANNEL_BINDING_FOCUS"]=="concurrent" {try await concurrentCase(source,same,other);return}
        if ProcessInfo.processInfo.environment["CHANNEL_BINDING_FOCUS"]=="seed-diagnostic" {
            let fixture=Fixture(source,same);await fixture.cookies.seed(source,Fixture.root)
            await fixture.begin(same)
            let written=GatewayChannels.token(Fixture.root,anchor:source,target:same)
            let initial=await fixture.cookies.value(source)
            await fixture.cookies.seed(source,Fixture.newRoot,httpOnly:false)
            let nonHTTPOnly=await fixture.cookies.value(source)
            await fixture.cookies.seed(source,Fixture.newRoot,httpOnly:true)
            let httpOnly=await fixture.cookies.value(source)
            let cookies=await fixture.cookies.data.httpCookieStore.allCookies()
            details["seedAttributeComparison"]=["initialIsProductionValue":initial==written,"nonHTTPOnlyReplaced":nonHTTPOnly==Fixture.newRoot,
                "httpOnlyReplaced":httpOnly==Fixture.newRoot,"cookieCount":cookies.count,"flags":cookies.map{["httpOnly":$0.isHTTPOnly,"secure":$0.isSecure,"sessionOnly":$0.isSessionOnly]}]
            fixture.active=false
            return
        }
        if ProcessInfo.processInfo.environment["CHANNEL_BINDING_FOCUS"] != "remaining" {
        for opening in [true,false] {
            for (from,to) in [(source,same),(source,http),(http,source),(source,other)] {
                let fixture=Fixture(from,to);await fixture.cookies.seed(from,Fixture.root)
                await fixture.begin(to,opening:opening)
                let accepted=opening ? fixture.connected==[to]:await fixture.finish()
                try check(accepted,"same gateway handoff: "+from+" -> "+to+" opening="+String(opening))
                try check(DeviceBinding.load(to)?.token==GatewayChannels.token(Fixture.root,anchor:from,target:to),"target binding committed")
                try check(await fixture.cookies.value(to)==GatewayChannels.token(Fixture.root,anchor:from,target:to),"target native cookie")
                passed.append("handoff:"+from+"->"+to+":"+String(opening))
            }
        }
        for opening in [true,false] {
            let foreign=Fixture(source,same,foreign:true);await foreign.cookies.seed(source,Fixture.root);await foreign.begin(same,opening:opening)
            try check(foreign.cookies.writes==0 && foreign.notices.contains("此地址的登录绑定已变化，请重新验证"),"different gateway rejected")
            let unknown=Fixture(source,other);await unknown.cookies.seed(source,Fixture.root);await unknown.cookies.seed(other,String(repeating:"u",count:64));await unknown.begin(other,opening:opening)
            try check(unknown.cookies.writes==0 && unknown.notices.contains("此地址的登录绑定已变化，请重新验证"),"unknown cookie rejected")
        }
        passed.append("different-gateway-and-unknown-cookie:both-entries")
        let rootAwait=Fixture(source,other);await rootAwait.cookies.seed(source,Fixture.root)
        rootAwait.cookies.afterRead={DeviceBinding.save(GatewayChannels.vault(Fixture.id),.init(name:Fixture.name,token:Fixture.newRoot))}
        await rootAwait.begin(other)
        try check(rootAwait.cookies.writes==0 && !rootAwait.peerPrepared,"root changed during cookie read: zero write")
        passed.append("root-await:zero-write");rootAwait.active=false
        }
        let rotated=Fixture(source,same);await rotated.cookies.seed(source,Fixture.root)
        let loginWritten=CallbackGate(),postWriteRead=CallbackGate()
        rotated.cookies.afterWrite={
            await rotated.cookies.seed(source,Fixture.newRoot,httpOnly:true)
            let accepted=await rotated.cookies.value(source)==Fixture.newRoot
            details["newLoginCookieAcceptedBeforeRoot"]=accepted
            DeviceBinding.save(GatewayChannels.vault(Fixture.id),.init(name:Fixture.name,token:Fixture.newRoot));loginWritten.hit()
            rotated.cookies.afterRead={postWriteRead.hit()}
        }
        await rotated.begin(same)
        let loginComplete=await loginWritten.wait(),observed=await postWriteRead.wait()
        let rotatedValue=await rotated.cookies.value(source)
        let rootNew=DeviceBinding.load(GatewayChannels.vault(Fixture.id))?.token==Fixture.newRoot
        details["loginDuringWrite"]=["loginCallbackCompleted":loginComplete,"postWriteReadCompleted":observed,"writes":rotated.cookies.writes,
            "peerPrepared":rotated.peerPrepared,"rootIsNew":rootNew,"cookieIsNew":rotatedValue==Fixture.newRoot,"targetBindingAbsent":DeviceBinding.load(same)==nil]
        try check(loginComplete && observed && !rotated.peerPrepared && rotated.cookies.writes==1 && rootNew && rotatedValue==Fixture.newRoot && DeviceBinding.load(same)==nil,"login during write callback wins")
        rotated.active=false
        passed.append("login-during-write:preserved")
        let rollback=Fixture(source,same);await rollback.cookies.seed(source,Fixture.root);await rollback.begin(same);await rollback.fail()
        try check(await rollback.cookies.value(source)==Fixture.root,"failed peer restores shared source slot")
        passed.append("peer-failure:rollback-source")
        let later=Fixture(source,same);await later.cookies.seed(source,Fixture.root);await later.begin(same);await later.cookies.seed(source,Fixture.newRoot,httpOnly:true);await later.fail()
        try check(await later.cookies.value(source)==Fixture.newRoot,"rollback preserves a later cookie even before vault refresh")
        passed.append("peer-failure:newer-cookie-preserved")
        let stale=Fixture(source,same);await stale.cookies.seed(source,Fixture.root);await stale.begin(same);stale.channelTicket+=1
        let staleAccepted=await stale.finish(),staleSource=await stale.cookies.value(source)
        try check(!staleAccepted && DeviceBinding.load(same)==nil && staleSource==DeviceBinding.load(source)?.token && stale.channelCookieOwners.isEmpty,"old ticket rejects commit and restores owned source slot")
        passed.append("old-ticket:rejected")
        let anchor=Fixture(source,same);await anchor.cookies.seed(source,Fixture.root);await anchor.begin(same);anchor.defaults.set(same,forKey:"channel-anchor:"+Fixture.id)
        try check(!(await anchor.finish()) && DeviceBinding.load(same)==nil,"changed anchor cannot commit")
        passed.append("anchor-change:rejected")
        // A real synchronous JavaScript prompt must not block WKHTTPCookieStore callbacks.
        let prompt=Fixture(source,same);await prompt.cookies.seed(source,Fixture.root);try await prompt.loadDocument();await prompt.begin(same)
        let originalTicket=prompt.channelTicket
        try await prompt.prepareHiddenTarget(same)
        try check(prompt.channelTicket==originalTicket,"hidden peer navigation must not invalidate source ticket")
        let response=try await prompt.prompt()
        try check(response=="accepted" && prompt.promptReplies==["accepted"],"real prompt commits once without deadlock")
        passed.append("real-prompt:accepted-once")
        let login=Fixture(source,same);await login.cookies.seed(source,Fixture.root);try await login.loadDocument();await login.begin(same)
        login.cookies.afterRead={
            await login.cookies.seed(source,Fixture.newRoot,httpOnly:true)
            details["promptLoginCookieAcceptedBeforeRoot"]=await login.cookies.value(source)==Fixture.newRoot
            DeviceBinding.save(GatewayChannels.vault(Fixture.id),.init(name:Fixture.name,token:Fixture.newRoot))
        }
        let rejected=try await login.prompt()
        let loginValue=await login.cookies.value(source)
        let promptRootNew=DeviceBinding.load(GatewayChannels.vault(Fixture.id))?.token==Fixture.newRoot
        details["loginDuringPrompt"]=["replies":login.promptReplies,"rootIsNew":promptRootNew,"cookieIsNew":loginValue==Fixture.newRoot,"writes":login.cookies.writes,"targetBindingAbsent":DeviceBinding.load(same)==nil]
        try check(rejected=="expired" && login.promptReplies==["expired"] && promptRootNew && loginValue==Fixture.newRoot && login.cookies.writes==1 && DeviceBinding.load(same)==nil,"prompt waiting login wins")
        passed.append("real-prompt:new-login-expired-once")
        let navigation=Fixture(source,same);await navigation.cookies.seed(source,Fixture.root);try await navigation.loadDocument();await navigation.begin(same)
        let navigated=CallbackGate(),navigationReadFinished=CallbackGate(),oldTicket=navigation.channelTicket
        navigation.cookies.afterRead={
            navigation.onCommit={navigated.hit()}
            navigation.web.loadHTMLString("<p>New document, same URL</p>",baseURL:URL(string:source+"/"))
            let committed=await navigated.wait(6);details["navigationCommitted"]=committed;navigationReadFinished.hit()
        }
        _ = try? await navigation.prompt()
        let readReturned=await navigationReadFinished.wait()
        details["navigationTicketChanged"]=navigation.channelTicket>oldTicket
        try check(readReturned && navigated.ready && navigation.channelTicket>oldTicket && navigation.promptReplies==["expired"] && DeviceBinding.load(same)==nil,"prompt waiting navigation expires once")
        passed.append("real-prompt:navigation-expired-once")
        let late=Fixture(source,same);await late.cookies.seed(source,Fixture.root);try await late.loadDocument();await late.begin(same)
        let lateRead=CallbackGate(),lateRollback=CallbackGate()
        late.cookies.afterRead={
            try? await Task.sleep(nanoseconds:5_000_000_000)
            late.cookies.afterWrite={lateRollback.hit()};lateRead.hit()
        }
        let timeout=try await late.prompt()
        let callbackReturned=await lateRead.wait(),rolledBack=await lateRollback.wait()
        try check(callbackReturned && rolledBack && timeout=="expired" && late.promptReplies==["expired"] && DeviceBinding.load(same)==nil,"late cookie callback cannot commit after native prompt deadline")
        passed.append("real-prompt:late-callback-expired-once")
        try await concurrentCase(source,same,other)
        if ProcessInfo.processInfo.environment["CHANNEL_BINDING_FOCUS"] != "remaining" {try await ownershipCases(source,same)}
    }
    static func ownershipCases(_ source:String,_ same:String) async throws{
        let ticket=Fixture(source,same);await ticket.cookies.seed(source,Fixture.root)
        ticket.cookies.afterWrite={ticket.channelTicket+=1}
        await ticket.begin(same)
        let restored=await ticket.cookies.value(source)
        details["ticketOnlyRollback"]=["writes":ticket.cookies.writes,"sourceMatchesSaved":restored==DeviceBinding.load(source)?.token,
            "targetBindingAbsent":DeviceBinding.load(same)==nil,"ownersEmpty":ticket.channelCookieOwners.isEmpty]
        try check(restored==Fixture.root && restored==DeviceBinding.load(source)?.token && ticket.cookies.writes==2 && !ticket.peerPrepared && DeviceBinding.load(same)==nil && ticket.channelCookieOwners.isEmpty,"ticket changed during setter callback safely restores source")
        passed.append("ownership:ticket-only-source-restored")

        let identical=Fixture(source,same);await identical.cookies.seed(source,Fixture.root);await identical.begin(same)
        let stale=identical.takeRollback();identical.channelTicket+=1
        identical.cookies.afterWrite={await stale()}
        await identical.begin(same)
        let before=identical.cookies.writes
        await stale()
        let newCommitted=await identical.finish()
        details["sameTokenNewOwner"]=["writes":identical.cookies.writes,"newCommitted":newCommitted,"oldRollbackWrote":identical.cookies.writes != before,"ownersEmpty":identical.channelCookieOwners.isEmpty]
        try check(before==2 && identical.cookies.writes==2 && newCommitted && identical.channelCookieOwners.isEmpty,"old rollback cannot touch a newer identical-token write")
        passed.append("ownership:new-same-token-owner-preserved")

        let between=Fixture(source,same);await between.cookies.seed(source,Fixture.root);await between.begin(same)
        let previous=between.takeRollback();between.channelTicket+=1
        var emptyBeforeNew=false
        between.cookies.afterWrite={
            emptyBeforeNew=between.channelCookieOwners.isEmpty
            between.channelTicket+=1;await between.begin(same)
        }
        await previous()
        let ownerRetained = !between.channelCookieOwners.isEmpty
        let accepted=await between.finish()
        let value=await between.cookies.value(same)
        details["newWriteBeforeRollbackCallback"]=["ownerReleasedAfterEnqueue":emptyBeforeNew,"newOwnerRetained":ownerRetained,"newCommitted":accepted,"writes":between.cookies.writes,"ownersEmpty":between.channelCookieOwners.isEmpty]
        try check(emptyBeforeNew && ownerRetained && accepted && between.cookies.writes==3 && value==GatewayChannels.token(Fixture.root,anchor:source,target:same) && between.channelCookieOwners.isEmpty,"new write between rollback enqueue and callback wins")
        passed.append("ownership:rollback-callback-new-operation-wins")

        let login=Fixture(source,same);await login.cookies.seed(source,Fixture.root);await login.begin(same)
        let old=login.takeRollback();login.channelTicket+=1
        var cookieAccepted=false
        login.cookies.afterWrite={
            await login.cookies.seed(source,Fixture.newRoot,httpOnly:true)
            cookieAccepted=await login.cookies.value(source)==Fixture.newRoot
            DeviceBinding.save(source,.init(name:Fixture.name,token:Fixture.newRoot))
            DeviceBinding.save(GatewayChannels.vault(Fixture.id),.init(name:Fixture.name,token:Fixture.newRoot))
        }
        await old()
        let finalCookie=await login.cookies.value(source)
        try check(cookieAccepted && finalCookie==Fixture.newRoot && DeviceBinding.load(source)?.token==Fixture.newRoot && DeviceBinding.load(GatewayChannels.vault(Fixture.id))?.token==Fixture.newRoot && login.channelCookieOwners.isEmpty,"new login during rollback callback wins")
        passed.append("ownership:rollback-callback-new-login-wins")
    }
    static func concurrentCase(_ source:String,_ same:String,_ other:String) async throws{
        let concurrent=Fixture(source,same)
        var descriptor=concurrent.channelRecord(source)!
        descriptor["endpoints"]=[source,same,other];concurrent.defaults.set(descriptor,forKey:"channels:"+source)
        await concurrent.cookies.seed(source,Fixture.root)
        concurrent.cookies.afterRead={concurrent.channelTicket+=1;await concurrent.begin(other)}
        await concurrent.begin(same)
        let newest=await concurrent.finish()
        details["concurrentSwitch"]=["allowedSecondTarget":concurrent.addresses(source).contains(other),"latestCommitted":newest,
            "newOrigin":concurrent.origin==other,"writes":concurrent.cookies.writes,"oldTargetBindingAbsent":DeviceBinding.load(same)==nil]
        try check(newest && concurrent.origin==other && concurrent.cookies.writes==1 && DeviceBinding.load(same)==nil,"only the latest concurrent switch writes or commits")
        passed.append("concurrent-switch:latest-only")
    }
}

@main final class TestApp:UIResponder,UIApplicationDelegate {
    var window:UIWindow?
    func application(_ application:UIApplication,didFinishLaunchingWithOptions options:[UIApplication.LaunchOptionsKey:Any]?=nil)->Bool{
        let view=UIViewController();window=UIWindow(frame:UIScreen.main.bounds);window?.rootViewController=view;window?.makeKeyAndVisible()
        Task{@MainActor in
            try? await Task.sleep(nanoseconds:200_000_000)
            var report:[String:Any]=["store":"real WKHTTPCookieStore nonPersistent","device":"iOS simulator","formalDataAccess":false]
            do{try await Suite.run();report["status"]="passed"}catch{report["status"]="failed";report["error"]=String(describing:error)}
            report["checks"]=Suite.passed;report["details"]=Suite.details;report["focus"]=ProcessInfo.processInfo.environment["CHANNEL_BINDING_FOCUS"] ?? "full";report["runId"]=ProcessInfo.processInfo.environment["CHANNEL_BINDING_RUN_ID"] ?? "missing"
            let data=try! JSONSerialization.data(withJSONObject:report,options:[.prettyPrinted,.sortedKeys])
            try! data.write(to:FileManager.default.urls(for:.documentDirectory,in:.userDomainMask)[0].appendingPathComponent("result.json"))
            print(String(decoding:data,as:UTF8.self));exit(report["status"] as? String=="passed" ? 0:1)
        }
        return true
    }
}
