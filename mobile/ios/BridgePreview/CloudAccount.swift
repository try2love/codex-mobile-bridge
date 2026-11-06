import Foundation
import Security
import UIKit

#if CMB_CLOUD_PROTOTYPE
@_silgen_name("cmb_cloud_call")
private func cloudCall(_ input: UnsafePointer<CChar>, _ state: UnsafePointer<CChar>, _ save: @convention(c) (UnsafePointer<CChar>?, UnsafeMutableRawPointer?) -> Int32, _ context: UnsafeMutableRawPointer?) -> UnsafeMutablePointer<CChar>?
@_silgen_name("cmb_cloud_free")
private func cloudFree(_ result: UnsafeMutablePointer<CChar>)

enum MobileCloud {
    static let queue = DispatchQueue(label: "bridge.cloud.management")
    static var enabled:Bool { UserDefaults.standard.bool(forKey:"cloud-account-enabled") }
    static var key:[String:Any] { [kSecClass as String:kSecClassGenericPassword,kSecAttrService as String:(Bundle.main.bundleIdentifier ?? "Bridge")+".cloud",kSecAttrAccount as String:"cloud-v1"] }
    static func run(_ input:[String:Any], _ done:@escaping (Result<[String:Any],Error>)->Void){
        queue.async {
            do {
                var data:CFTypeRef?
                var query=key;query[kSecReturnData as String]=true
                let status=SecItemCopyMatching(query as CFDictionary,&data)
                guard status==errSecSuccess || status==errSecItemNotFound else {throw CloudFailure("云登录存储暂不可用")}
                let saved=String(decoding:(data as? Data) ?? Data(),as:UTF8.self)
                let raw=try JSONSerialization.data(withJSONObject:input)
                let result=String(decoding:raw,as:UTF8.self).withCString { command in saved.withCString { vault in
                    cloudCall(command,vault,{ text,_ in
                        guard let text=text else {return 0}
                        let bytes=Data(bytes:text,count:strlen(text))
                        let values:[String:Any]=[kSecValueData as String:bytes,kSecAttrAccessible as String:kSecAttrAccessibleWhenUnlockedThisDeviceOnly]
                        var result=SecItemUpdate(MobileCloud.key as CFDictionary,values as CFDictionary)
                        if result==errSecItemNotFound {result=SecItemAdd(MobileCloud.key.merging(values){_,b in b} as CFDictionary,nil)}
                        return result==errSecSuccess ? 1 : 0
                    },nil)
                }}
                guard let result=result else {throw CloudFailure("云服务不可用")}
                defer {cloudFree(result)}
                let response=try JSONSerialization.jsonObject(with:Data(bytes:result,count:strlen(result))) as? [String:Any]
                guard response?["ok"] as? Bool==true,let value=response?["result"] as? [String:Any] else {throw CloudFailure(response?["error"] as? String ?? "云服务请求未完成")}
                DispatchQueue.main.async {done(.success(value))}
            }catch{ DispatchQueue.main.async {done(.failure(error))} }
        }
    }
}
final class CloudLoginForm:UIViewController {
    private let type=UISegmentedControl(items:[MobileStrings.text("官方云服务"), MobileStrings.text("自建云服务")])
    private let server=UITextField(), login=UITextField(), password=UITextField()
    var profile:[String:Any]?
    var submit:(([String:Any])->Void)?
    override func viewDidLoad(){
        super.viewDidLoad();title=MobileStrings.text("登录云账号");view.backgroundColor = .systemBackground
        navigationItem.leftBarButtonItem=UIBarButtonItem(title:MobileStrings.text("取消"),style:.plain,target:self,action:#selector(cancel))
        navigationItem.rightBarButtonItem=UIBarButtonItem(title:MobileStrings.text("登录"),style:.done,target:self,action:#selector(send))
        let stack=UIStackView(arrangedSubviews:[type,server,login,password]);stack.axis = .vertical;stack.spacing=14;stack.translatesAutoresizingMaskIntoConstraints=false;view.addSubview(stack)
        NSLayoutConstraint.activate([stack.topAnchor.constraint(equalTo:view.safeAreaLayoutGuide.topAnchor,constant:22),stack.leadingAnchor.constraint(equalTo:view.layoutMarginsGuide.leadingAnchor),stack.trailingAnchor.constraint(equalTo:view.layoutMarginsGuide.trailingAnchor)])
        for (field,hint) in [(server,"服务器地址"),(login,"账号"),(password,"密码")]{
            field.borderStyle = .roundedRect;field.placeholder=MobileStrings.text(hint);field.autocorrectionType = .no;field.autocapitalizationType = .none
            field.heightAnchor.constraint(greaterThanOrEqualToConstant:44).isActive=true
            field.addTarget(self,action:#selector(validateCloudForm),for:.editingChanged)
        }
        server.keyboardType = .URL;login.textContentType = .username;password.textContentType = .password;password.isSecureTextEntry=true
        type.selectedSegmentIndex=profile?["serverType"] as? String=="custom" ? 1 : 0
        type.addTarget(self,action:#selector(modeChanged),for:.valueChanged);modeChanged()
        login.text=profile?["login"] as? String;server.text=profile?["customOrigin"] as? String;validateCloudForm()
    }
    @objc private func modeChanged(){server.isHidden=type.selectedSegmentIndex==0;server.text="";password.text="";validateCloudForm()}
    @objc private func validateCloudForm(){navigationItem.rightBarButtonItem?.isEnabled = login.hasText && password.hasText && (type.selectedSegmentIndex==0 || server.hasText)}
    @objc private func cancel(){password.text="";dismiss(animated:true)}
    @objc private func send(){
        var input:[String:Any]=["op":"login","serverType":type.selectedSegmentIndex==0 ? "official":"custom","login":(login.text ?? "").trimmingCharacters(in:.whitespacesAndNewlines),"password":password.text ?? ""]
        if type.selectedSegmentIndex==1 {input["origin"]=(server.text ?? "").trimmingCharacters(in:.whitespacesAndNewlines)}
        password.text="";let callback=submit;dismiss(animated:true){callback?(input)}
    }
}
private struct CloudFailure:LocalizedError {
    let message:String
    init(_ message:String){self.message=message}
    var errorDescription:String?{message}
}
#endif
