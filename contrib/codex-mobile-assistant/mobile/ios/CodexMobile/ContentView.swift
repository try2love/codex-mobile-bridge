import SwiftUI
import WebKit

struct BrowserView: UIViewRepresentable {
    let webView: WKWebView
    func makeUIView(context: Context) -> WKWebView { webView }
    func updateUIView(_ view: WKWebView, context: Context) {}
}

struct ContentView: View {
    @ObservedObject var model: ConnectionModel
    @State private var confirmClear = false
    @State private var confirmReload = false
    @State private var showScanner = false
    @State private var scannedText: String?
    @State private var scanError = false
    @State private var scannedAddress = ""
    @State private var confirmScannedConnection = false
    private let canvas = Color(red: 0.98, green: 0.98, blue: 0.976)

    var body: some View {
        ZStack {
            canvas.ignoresSafeArea()
            VStack(spacing: 0) {
                if !model.chatPage || model.showSettings || model.connectionError != nil { homeToolbar }
                ZStack {
                    VStack(spacing: 0) {
                        ZStack {
                            Color.clear
                            if model.loading { ProgressView().progressViewStyle(.linear) }
                        }.frame(height: model.chatPage && !model.loading ? 0 : 3)
                        ZStack {
                            BrowserView(webView: model.webView)
                            if let message = model.connectionError {
                                VStack(spacing: 18) {
                                    Image(systemName: "wifi.exclamationmark").font(.system(size: 36))
                                    Text("暂时无法连接").font(.title2.bold())
                                    Text(message).font(.subheadline).foregroundStyle(.secondary)
                                        .multilineTextAlignment(.center)
                                    Button("重新连接") { model.reload() }
                                        .buttonStyle(PrimaryButtonStyle())
                                    Button("修改连接地址") { model.showSettings = true }
                                        .padding(10)
                                }.padding(32).frame(maxWidth: .infinity, maxHeight: .infinity)
                                    .background(canvas)
                            }
                        }
                    }.opacity(model.showSettings ? 0 : 1).accessibilityHidden(model.showSettings)
                    if model.showSettings {
                        settings.background(canvas)
                    }
                }
            }
            if model.clearing {
                canvas.opacity(0.96).ignoresSafeArea()
                ProgressView("正在清除登录数据…")
            }
        }
        .tint(Color(white: 0.12))
        .onAppear { model.startIfNeeded() }
        .onChange(of: model.pendingHomeAction) { action in
            if action == "reload" { confirmReload = true }
            if action == "clear" { confirmClear = true }
            model.pendingHomeAction = ""
        }
        .confirmationDialog("连接菜单", isPresented: $model.fallbackHomeMenu, titleVisibility: .visible) {
            Button("修改连接地址") { model.showSettings = true }
            Button("重新加载页面") { confirmReload = true }
            Button(model.systemNotificationsEnabled ? "关闭系统任务通知" : "开启系统任务通知") { model.toggleTaskNotifications() }
            Button(model.backgroundTaskNotifications ? "关闭后台任务提醒" : "开启后台任务提醒（实验）") { model.backgroundTaskNotifications.toggle() }
            Button("清除本机登录数据", role: .destructive) { confirmClear = true }
            Button("取消", role: .cancel) {}
        }
        .fullScreenCover(isPresented: $showScanner, onDismiss: {
            guard let value = scannedText else { return }
            scannedText = nil
            if let address = model.connectionAddress(fromQRCode: value) {
                scannedAddress = address
                confirmScannedConnection = true
            } else { scanError = true }
        }) {
            QRCodeScanner(onResult: { value in
                scannedText = value
                showScanner = false
            }, onCancel: { showScanner = false })
        }
        .alert("无法使用这个二维码", isPresented: $scanError) {
            Button("好", role: .cancel) {}
        } message: {
            Text("请扫描电脑助手生成的 HTTPS 连接二维码，当前地址未更改。")
        }
        .alert("连接到这个入口？", isPresented: $confirmScannedConnection) {
            Button("取消", role: .cancel) {}
            Button("连接") {
                model.address = scannedAddress
                model.connect()
            }
        } message: { Text(scannedAddress) }
        .alert("清除本机登录数据？", isPresented: $confirmClear) {
            Button("取消", role: .cancel) {}
            Button("清除", role: .destructive) { model.clearLogin() }
        } message: {
            Text("清除本 App 所有地址的登录状态和网页缓存，下次需重新登录。电脑上的聊天不会删除。")
        }
        .alert("重新加载页面？", isPresented: $confirmReload) {
            Button("取消", role: .cancel) {}
            Button("重新加载") { model.reload() }
        } message: { Text("重新加载会重建当前页面。") }
    }

    private var homeToolbar: some View {
        HStack(spacing: 8) {
            Image("Brand").resizable().frame(width: 24, height: 24)
            Text(model.pageTitle)
                .font(.system(size: 13)).lineLimit(1).truncationMode(.middle)
            if !model.showSettings && model.desktopConnection != "idle" {
                HStack(spacing: 4) {
                    if model.desktopConnection == "connecting" {
                        ProgressView().controlSize(.mini)
                    } else {
                        Circle().fill(model.desktopConnection == "connected" ? Color.green : Color.orange)
                            .frame(width: 6, height: 6)
                    }
                    Text(model.desktopConnection == "connected" ? "已连接" : model.desktopConnection == "connecting" ? "连接中" : "未连接")
                        .font(.system(size: 10)).foregroundStyle(model.desktopConnection == "connected" ? Color(red: 0.15, green: 0.5, blue: 0.3) : Color.secondary)
                }.fixedSize()
            }
            Spacer(minLength: 0)
            if !model.connected {
                Button {
                    scannedText = nil
                    showScanner = true
                } label: {
                    Label("扫码连接", systemImage: "qrcode.viewfinder")
                        .font(.system(size: 13, weight: .medium)).fixedSize()
                        .frame(minHeight: 44)
                }.accessibilityLabel("扫码连接")
            }
            if model.currentURL != nil {
                Button {
                    model.openHomeMenu()
                } label: {
                    Image(systemName: "ellipsis").font(.title2)
                        .frame(width: 44, height: 44).contentShape(Rectangle())
                }.accessibilityLabel("连接菜单")
            }
        }.padding(.horizontal, 16).frame(height: 48)
    }

    private var settings: some View {
        ScrollView {
            VStack(spacing: 0) {
                Image("Brand").resizable().frame(width: 76, height: 76)
                Text("随时，继续对话").font(.system(size: 28, weight: .bold)).padding(.top, 22)
                Text("连接电脑上的 Codex 手机助手").font(.subheadline)
                    .foregroundStyle(.secondary).padding(.top, 10)
                HStack(spacing: 8) {
                    ForEach(ConnectionModel.Mode.allCases, id: \.self) { mode in
                        Button {
                            model.mode = mode
                            model.addressError = nil
                        } label: {
                            Text(mode.title).font(.system(size: 15, weight: .medium))
                                .frame(maxWidth: .infinity).frame(minHeight: 50)
                                .background(model.mode == mode ? Color(white: 0.12) : Color(white: 0.93))
                                .foregroundStyle(model.mode == mode ? .white : Color(white: 0.35))
                                .clipShape(RoundedRectangle(cornerRadius: 14))
                        }
                        .accessibilityAddTraits(model.mode == mode ? .isSelected : [])
                    }
                }.padding(.top, 32)
                Text(model.mode.hint).font(.system(size: 13)).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(.top, 14)
                Text("连接地址").font(.system(size: 14, weight: .medium))
                    .frame(maxWidth: .infinity, alignment: .leading).padding(.top, 24)
                TextField("https://…", text: Binding(get: { model.address }, set: { model.address = $0 }))
                    .font(.system(size: 16)).keyboardType(.URL).textContentType(.URL)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .submitLabel(.go).onSubmit { model.connect() }
                    .padding(16).background(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 16))
                    .overlay(RoundedRectangle(cornerRadius: 16).stroke(Color(white: 0.87), lineWidth: 1))
                    .padding(.top, 10).accessibilityLabel("连接地址")
                HStack {
                    Spacer()
                    PasteButton(payloadType: String.self) { values in
                        if let value = values.first { model.address = value }
                    }.labelStyle(.titleOnly).padding(.vertical, 10)
                        .environment(\.locale, Locale(identifier: "zh_CN"))
                }
                Toggle("下次打开自动连接", isOn: $model.autoConnect).font(.system(size: 14))
                    .padding(.vertical, 12)
                Toggle("后台任务提醒（实验）", isOn: $model.backgroundTaskNotifications)
                    .font(.system(size: 14)).padding(.vertical, 10)
                Text(model.backgroundTaskReminderStatus).font(.footnote).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Text("仅适用于 TrollStore 安装；后台运行会增加耗电，重启手机后需打开一次助手。")
                    .font(.caption).foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.top, 6)
                if let error = model.addressError {
                    Text(error).font(.footnote).foregroundStyle(.red)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                Button("连接并开始聊天") { model.connect() }
                    .buttonStyle(PrimaryButtonStyle()).padding(.top, 14)
                if model.currentURL != nil {
                    Button("返回当前聊天") { model.showSettings = false }
                        .frame(minHeight: 48)
                }
                Text("登录时勾选「记住登录」，同一地址下 30 天免输密码。\n电脑、Codex 和助手需保持运行。")
                    .font(.system(size: 12)).foregroundStyle(.secondary)
                    .multilineTextAlignment(.center).lineSpacing(5).padding(.top, 24)
            }.padding(.horizontal, 28).padding(.top, 36).padding(.bottom, 28)
                .frame(maxWidth: 520).frame(maxWidth: .infinity)
        }.scrollDismissesKeyboard(.interactively)
    }
}

private struct PrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(.system(size: 16, weight: .semibold))
            .frame(maxWidth: .infinity).frame(minHeight: 54)
            .foregroundStyle(.white).background(Color(white: configuration.isPressed ? 0.3 : 0.12))
            .clipShape(RoundedRectangle(cornerRadius: 16))
    }
}
