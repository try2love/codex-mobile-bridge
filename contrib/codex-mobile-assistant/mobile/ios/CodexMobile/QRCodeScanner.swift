import AVFoundation
import SwiftUI
import UIKit

struct QRCodeScanner: UIViewControllerRepresentable {
    let onResult: (String) -> Void
    let onCancel: () -> Void

    func makeUIViewController(context: Context) -> QRScannerController {
        QRScannerController(onResult: onResult, onCancel: onCancel)
    }

    func updateUIViewController(_ controller: QRScannerController, context: Context) {}

    static func dismantleUIViewController(_ controller: QRScannerController, coordinator: ()) {
        controller.stopScanning()
    }
}

// Camera configuration and start/stop are serialized away from the main thread.
private final class QRScannerEngine: NSObject, AVCaptureMetadataOutputObjectsDelegate {
    let session = AVCaptureSession()
    var onCode: ((String) -> Void)?
    var onError: (() -> Void)?
    var onStarted: (() -> Void)?
    private let queue = DispatchQueue(label: "io.github.abyxs.codexmobile.qr-camera")
    private var configured = false

    func start() {
        queue.async { [weak self] in
            guard let self else { return }
            if !self.configured {
                self.session.beginConfiguration()
                do {
                    guard let camera = AVCaptureDevice.default(
                        .builtInWideAngleCamera, for: .video, position: .back)
                        ?? AVCaptureDevice.default(for: .video) else { throw CameraError.unavailable }
                    let input = try AVCaptureDeviceInput(device: camera)
                    let output = AVCaptureMetadataOutput()
                    guard self.session.canAddInput(input) else { throw CameraError.unavailable }
                    self.session.addInput(input)
                    guard self.session.canAddOutput(output) else { throw CameraError.unavailable }
                    self.session.addOutput(output)
                    guard output.availableMetadataObjectTypes.contains(.qr) else {
                        throw CameraError.unavailable
                    }
                    output.setMetadataObjectsDelegate(self, queue: .main)
                    output.metadataObjectTypes = [.qr]
                    self.configured = true
                } catch {
                    self.session.inputs.forEach { self.session.removeInput($0) }
                    self.session.outputs.forEach { self.session.removeOutput($0) }
                }
                self.session.commitConfiguration()
            }
            guard self.configured else {
                DispatchQueue.main.async { [weak self] in self?.onError?() }
                return
            }
            if !self.session.isRunning { self.session.startRunning() }
            DispatchQueue.main.async { [weak self] in self?.onStarted?() }
        }
    }

    func stop() {
        queue.async { [weak self] in
            guard let self else { return }
            if self.session.isRunning { self.session.stopRunning() }
        }
    }

    func metadataOutput(_ output: AVCaptureMetadataOutput,
                        didOutput objects: [AVMetadataObject], from connection: AVCaptureConnection) {
        if let value = objects.compactMap({ ($0 as? AVMetadataMachineReadableCodeObject)?.stringValue }).first {
            onCode?(value)
        }
    }

    deinit {
        let captureSession = session
        queue.async {
            if captureSession.isRunning { captureSession.stopRunning() }
        }
    }

    private enum CameraError: Error { case unavailable }
}

final class QRScannerController: UIViewController {
    private let engine = QRScannerEngine()
    private lazy var preview = AVCaptureVideoPreviewLayer(session: engine.session)
    private let onResult: (String) -> Void
    private let onCancel: () -> Void
    private var visible = false
    private var finished = false
    private let guide = UIView()
    private let hint = UILabel()
    private let errorPanel = UIStackView()
    private let errorLabel = UILabel()
    private let settingsButton = UIButton(type: .system)
    private let retryButton = UIButton(type: .system)

    init(onResult: @escaping (String) -> Void, onCancel: @escaping () -> Void) {
        self.onResult = onResult
        self.onCancel = onCancel
        super.init(nibName: nil, bundle: nil)
    }

    required init?(coder: NSCoder) { fatalError("Storyboard initialization is not used") }

    override var preferredStatusBarStyle: UIStatusBarStyle { .lightContent }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        preview.videoGravity = .resizeAspectFill
        view.layer.insertSublayer(preview, at: 0)

        let close = UIButton(type: .system)
        close.setTitle("取消", for: .normal)
        close.setTitleColor(.white, for: .normal)
        close.titleLabel?.font = .systemFont(ofSize: 17)
        close.addTarget(self, action: #selector(cancel), for: .touchUpInside)
        let title = UILabel()
        title.text = "扫码连接"
        title.textColor = .white
        title.font = .systemFont(ofSize: 18, weight: .semibold)
        guide.layer.borderColor = UIColor.white.cgColor
        guide.layer.borderWidth = 2
        guide.layer.cornerRadius = 24
        guide.isUserInteractionEnabled = false
        guide.accessibilityLabel = "二维码识别框"
        hint.text = "将电脑助手的连接二维码放入框内"
        hint.textColor = .white
        hint.font = .systemFont(ofSize: 15)
        hint.textAlignment = .center
        hint.numberOfLines = 2
        hint.layer.shadowColor = UIColor.black.cgColor
        hint.layer.shadowOpacity = 0.8
        hint.layer.shadowRadius = 4
        hint.layer.shadowOffset = .zero

        errorLabel.textColor = .white
        errorLabel.font = .systemFont(ofSize: 16)
        errorLabel.textAlignment = .center
        errorLabel.numberOfLines = 0
        settingsButton.setTitle("打开相机权限设置", for: .normal)
        settingsButton.setTitleColor(.white, for: .normal)
        settingsButton.addTarget(self, action: #selector(openSettings), for: .touchUpInside)
        retryButton.setTitle("重试", for: .normal)
        retryButton.setTitleColor(.white, for: .normal)
        retryButton.addTarget(self, action: #selector(activateCamera), for: .touchUpInside)
        errorPanel.axis = .vertical
        errorPanel.spacing = 18
        errorPanel.backgroundColor = UIColor.black.withAlphaComponent(0.85)
        errorPanel.layer.cornerRadius = 20
        errorPanel.isLayoutMarginsRelativeArrangement = true
        errorPanel.layoutMargins = UIEdgeInsets(top: 24, left: 24, bottom: 24, right: 24)
        [errorLabel, settingsButton, retryButton].forEach { errorPanel.addArrangedSubview($0) }
        errorPanel.isHidden = true

        [close, title, guide, hint, errorPanel].forEach {
            $0.translatesAutoresizingMaskIntoConstraints = false
            view.addSubview($0)
        }
        let safe = view.safeAreaLayoutGuide
        let guideWidth = guide.widthAnchor.constraint(equalToConstant: 250)
        guideWidth.priority = .defaultHigh
        NSLayoutConstraint.activate([
            close.leadingAnchor.constraint(equalTo: safe.leadingAnchor, constant: 16),
            close.topAnchor.constraint(equalTo: safe.topAnchor, constant: 8),
            close.widthAnchor.constraint(greaterThanOrEqualToConstant: 60),
            close.heightAnchor.constraint(equalToConstant: 44),
            title.centerXAnchor.constraint(equalTo: safe.centerXAnchor),
            title.centerYAnchor.constraint(equalTo: close.centerYAnchor),
            guide.centerXAnchor.constraint(equalTo: safe.centerXAnchor),
            guide.centerYAnchor.constraint(equalTo: safe.centerYAnchor),
            guideWidth,
            guide.widthAnchor.constraint(lessThanOrEqualTo: safe.widthAnchor, multiplier: 0.7),
            guide.heightAnchor.constraint(equalTo: guide.widthAnchor),
            guide.heightAnchor.constraint(lessThanOrEqualTo: safe.heightAnchor, multiplier: 0.55),
            hint.topAnchor.constraint(equalTo: guide.bottomAnchor, constant: 18),
            hint.leadingAnchor.constraint(equalTo: safe.leadingAnchor, constant: 24),
            hint.trailingAnchor.constraint(equalTo: safe.trailingAnchor, constant: -24),
            errorPanel.centerYAnchor.constraint(equalTo: safe.centerYAnchor),
            errorPanel.leadingAnchor.constraint(equalTo: safe.leadingAnchor, constant: 24),
            errorPanel.trailingAnchor.constraint(equalTo: safe.trailingAnchor, constant: -24),
            settingsButton.heightAnchor.constraint(greaterThanOrEqualToConstant: 44),
            retryButton.heightAnchor.constraint(greaterThanOrEqualToConstant: 44),
        ])
        engine.onCode = { [weak self] code in
            guard let self, self.visible, !self.finished else { return }
            self.finished = true
            self.engine.stop()
            self.onResult(code)
        }
        engine.onError = { [weak self] in self?.cameraFailed() }
        engine.onStarted = { [weak self] in self?.view.setNeedsLayout() }
        NotificationCenter.default.addObserver(self, selector: #selector(activateCamera),
            name: UIApplication.didBecomeActiveNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(stopScanning),
            name: UIApplication.didEnterBackgroundNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(cameraFailed),
            name: .AVCaptureSessionRuntimeError, object: engine.session)
        NotificationCenter.default.addObserver(self, selector: #selector(cameraFailed),
            name: .AVCaptureSessionWasInterrupted, object: engine.session)
        NotificationCenter.default.addObserver(self, selector: #selector(activateCamera),
            name: .AVCaptureSessionInterruptionEnded, object: engine.session)
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        visible = true
        activateCamera()
    }

    override func viewWillDisappear(_ animated: Bool) {
        visible = false
        stopScanning()
        super.viewWillDisappear(animated)
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        preview.frame = view.bounds
        guard let connection = preview.connection, connection.isVideoOrientationSupported,
              let orientation = view.window?.windowScene?.interfaceOrientation else { return }
        switch orientation {
        case .landscapeLeft: connection.videoOrientation = .landscapeLeft
        case .landscapeRight: connection.videoOrientation = .landscapeRight
        case .portraitUpsideDown: connection.videoOrientation = .portraitUpsideDown
        default: connection.videoOrientation = .portrait
        }
    }

    @objc private func activateCamera() {
        // Capture-session notifications can arrive on its background queue.
        if !Thread.isMainThread {
            DispatchQueue.main.async { [weak self] in self?.activateCamera() }
            return
        }
        guard visible, !finished,
              UIApplication.shared.applicationState == .active else { return }
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            errorPanel.isHidden = true
            guide.isHidden = false
            hint.isHidden = false
            engine.start()
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .video) { [weak self] _ in
                DispatchQueue.main.async { self?.activateCamera() }
            }
        default:
            engine.stop()
            showError("允许使用相机后即可扫码，也可以取消并粘贴连接地址。", needsSettings: true)
        }
    }

    @objc private func cameraFailed() {
        if !Thread.isMainThread {
            DispatchQueue.main.async { [weak self] in self?.cameraFailed() }
            return
        }
        guard visible, !finished else { return }
        engine.stop()
        showError("相机暂时无法使用，请关闭其他占用相机的应用后重试，或取消并粘贴地址。", needsSettings: false)
    }

    private func showError(_ message: String, needsSettings: Bool) {
        errorLabel.text = message
        settingsButton.isHidden = !needsSettings
        retryButton.isHidden = needsSettings
        errorPanel.isHidden = false
        guide.isHidden = true
        hint.isHidden = true
    }

    @objc private func openSettings() {
        guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
        UIApplication.shared.open(url)
    }

    @objc private func cancel() {
        guard !finished else { return }
        finished = true
        engine.stop()
        onCancel()
    }

    @objc func stopScanning() { engine.stop() }

    deinit { NotificationCenter.default.removeObserver(self) }
}
