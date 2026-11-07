mod session;
mod tls;

use std::sync::{Arc, OnceLock, RwLock};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use base64::{Engine as _, engine::general_purpose::STANDARD};
use futures_util::{SinkExt, StreamExt};
use reqwest::{Client, Method, Response};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use tokio::net::TcpStream;
use tokio_tungstenite::{
    MaybeTlsStream, WebSocketStream,
    tungstenite::{Message, client::IntoClientRequest, protocol::WebSocketConfig},
};
use tokio_util::sync::CancellationToken;
use uuid::Uuid;
use zeroize::Zeroize;

use crate::api::types::*;
use crate::cloud_crypto::{self, ControlCipher, Frame, MAX_CHUNK};
use crate::error::{BridgeError, Result};
use crate::transport::{MAX_FILE, Transport};

fn invalid() -> BridgeError {
    BridgeError::Protocol("encrypted relay authentication failed".into())
}
fn checked<T>(value: anyhow::Result<T>) -> Result<T> {
    value.map_err(|_| invalid())
}
fn now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64
}
fn id(value: &str) -> Result<Uuid> {
    Uuid::parse_str(value).map_err(|_| invalid())
}
fn b64(value: &[u8]) -> String {
    STANDARD.encode(value)
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct AccountSecrets {
    origin: String,
    account_id: String,
    login: String,
    access_token: String,
    refresh_token: String,
    #[serde(default)]
    refresh_request_id: String,
    access_deadline: i64,
    installation_id: String,
    control_private: String,
    #[serde(default)]
    device_token: String,
}
impl Drop for AccountSecrets {
    fn drop(&mut self) {
        self.access_token.zeroize();
        self.refresh_token.zeroize();
        self.control_private.zeroize();
        self.device_token.zeroize();
    }
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct BindingSecrets {
    connection: RelayConnection,
    installation_id: String,
    device_token: String,
    control_private: String,
    control_public: String,
    preview_private: String,
    preview_public: String,
    desktop_preview_public: String,
    device_name: String,
    pairing_code: String,
}
impl Drop for BindingSecrets {
    fn drop(&mut self) {
        self.device_token.zeroize();
        self.control_private.zeroize();
        self.preview_private.zeroize();
    }
}

fn websocket_url(origin: &str) -> Result<String> {
    let url = checked(cloud_crypto::exact_origin(origin))?;
    let scheme = if url.scheme() == "https" { "wss" } else { "ws" };
    Ok(format!(
        "{scheme}://{}/v1/ws/mobile",
        origin.split_once("://").unwrap().1
    ))
}

fn api_client() -> Result<Client> {
    static CLIENT: OnceLock<Client> = OnceLock::new();
    if let Some(client) = CLIENT.get() {
        return Ok(client.clone());
    }
    let client = Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .retry(reqwest::retry::never())
        .connect_timeout(Duration::from_secs(10))
        .timeout(Duration::from_secs(20))
        .user_agent("BridgeMobile/1")
        .build()?;
    let _ = CLIENT.set(client.clone());
    Ok(client)
}

async fn api(
    origin: &str,
    path: &str,
    method: Method,
    body: Option<Value>,
    token: &str,
) -> Result<Value> {
    checked(cloud_crypto::exact_origin(origin))?;
    if !path.starts_with("/v1/") || path.contains(['?', '#', '\\']) || path.contains("..") {
        return Err(invalid());
    }
    let mut request = api_client()?.request(method, format!("{origin}{path}"));
    if let Some(body) = body {
        request = request.json(&body);
    }
    if !token.is_empty() {
        request = request.bearer_auth(token);
    }
    let response = request.send().await?;
    if response.status().is_redirection() {
        return Err(BridgeError::Redirect);
    }
    let status = response.status();
    let bytes = Transport::bounded_bytes(response, 65536).await?;
    let value: Value = serde_json::from_slice(&bytes)?;
    if !status.is_success() {
        return Err(BridgeError::Http {
            status: status.as_u16(),
            code: value["code"].as_str().unwrap_or("unavailable").into(),
            message: "Cloud account or service access is unavailable".into(),
        });
    }
    Ok(value)
}

pub(crate) async fn capabilities(origin: &str) -> Result<CloudCapabilities> {
    let v = api(origin, "/v1/capabilities", Method::GET, None, "").await?;
    if !v["relay_protocols"]
        .as_array()
        .is_some_and(|a| a.contains(&json!(2)))
    {
        return Err(BridgeError::Relay("protocol_upgrade_required".into()));
    }
    if v["control_noise"] != "Noise_KK_25519_ChaChaPoly_BLAKE2s"
        || v["preview_noise"] != "Noise_K_25519_ChaChaPoly_BLAKE2s"
    {
        return Err(invalid());
    }
    Ok(CloudCapabilities {
        mode: v["mode"].as_str().unwrap_or_default().into(),
        login: v["login"].as_str().unwrap_or_default().into(),
        registration: v["registration"] == true,
        entitlement_required: v["entitlement_required"] == true,
        umeng: v["push"]["umeng"] == true,
        apns: v["push"]["apns"] == true,
        umeng_app_id: v["push"]["umeng_app_id"]
            .as_str()
            .unwrap_or_default()
            .into(),
        apns_app_id: v["push"]["apns_app_id"].as_str().unwrap_or_default().into(),
        automatic_discovery: v["device_discovery"] == "account_directory_v1"
            && v["binding_access"] == "desktop_switch_v1",
    })
}

pub(crate) async fn login(
    origin: String,
    login: String,
    mut password: String,
    saved: Vec<String>,
    reserved_accounts: &[&str],
) -> Result<CloudLogin> {
    capabilities(&origin).await?;
    let response = api(
        &origin,
        "/v1/auth/login",
        Method::POST,
        Some(json!({"login":login,"password":password})),
        "",
    )
    .await;
    password.zeroize();
    let v = response?;
    let account = v["account"]["id"].as_str().ok_or_else(invalid)?.to_owned();
    id(&account)?;
    if reserved_accounts.contains(&account.as_str()) {
        // The authenticated target already has a different local configuration;
        // never enroll or rotate that configuration's installation.
        if let Some(token) = v["access_token"].as_str() {
            let _ = api(&origin, "/v1/auth/logout", Method::POST, Some(json!({})), token).await;
        }
        return Err(BridgeError::Invalid("这个云账号已有独立配置，请使用对应配置".into()));
    }

    // Select the installation by the verified server identity, never by a typed login label.
    let prior = saved.iter().map(|raw|serde_json::from_str::<AccountSecrets>(raw))
        .collect::<std::result::Result<Vec<_>,_>>()?.into_iter()
        .find(|s| s.origin==origin && s.account_id==account);
    let mut secret = match prior {
        Some(prior) if prior.origin == origin && prior.account_id == account => prior,
        _ => {
            let key = checked(cloud_crypto::new_keypair())?;
            AccountSecrets {
                origin: origin.clone(),
                account_id: account.clone(),
                login: String::new(),
                access_token: String::new(),
                refresh_token: String::new(),
                refresh_request_id: String::new(),
                access_deadline: 0,
                installation_id: Uuid::new_v4().to_string(),
                control_private: b64(&key.private),
                device_token: String::new(),
            }
        }
    };
    secret.login = v["account"]["login"].as_str().ok_or_else(invalid)?.into();
    secret.access_token = v["access_token"].as_str().ok_or_else(invalid)?.into();
    secret.refresh_token = v["refresh_token"].as_str().ok_or_else(invalid)?.into();
    secret.refresh_request_id.clear();
    secret.access_deadline = now() + v["expires_in"].as_i64().ok_or_else(invalid)?;
    enroll_installation(&mut secret).await?;
    Ok(CloudLogin {
        account_id: secret.account_id.clone(),
        login: secret.login.clone(),
        credentials: serde_json::to_string(&secret)?,
    })
}

async fn enroll_installation(account: &mut AccountSecrets) -> Result<()> {
    let private = checked(cloud_crypto::key(&account.control_private))?;
    let public = checked(cloud_crypto::public_key(&private))?;
    let device = api(
        &account.origin,
        "/v1/installations/enroll",
        Method::POST,
        Some(json!({"id":account.installation_id,"control_public_key":b64(&public),"name":
            if cfg!(target_os = "android") { "Android phone" } else if cfg!(target_os = "ios") { "iPhone" } else { "Phone" }})),
        &account.access_token,
    )
    .await?;
    if device["id"] != account.installation_id
        || device["account_id"] != account.account_id
        || device["control_public_key"] != b64(&public)
    {
        return Err(invalid());
    }
    account.device_token = device["device_token"]
        .as_str()
        .filter(|s| !s.is_empty())
        .ok_or_else(invalid)?
        .into();
    Ok(())
}

pub(crate) fn credential_state(
    raw: &str,
    origin: &str,
    account: &str,
) -> Result<CloudCredentialState> {
    checked(cloud_crypto::exact_origin(origin))?;
    let mut secret: AccountSecrets = serde_json::from_str(raw)?;
    if secret.origin != origin || secret.account_id != account {
        return Err(invalid());
    }
    id(account)?;
    let consumed_credentials = if secret.access_deadline <= now() + 30 {
        if secret.refresh_token.is_empty() {
            return Err(BridgeError::Unauthenticated);
        }
        if secret.refresh_request_id.is_empty() {
            secret.refresh_request_id = Uuid::new_v4().to_string();
        }
        Some(serde_json::to_string(&secret)?)
    } else {
        None
    };
    Ok(CloudCredentialState {
        origin: origin.into(),
        account_id: account.into(),
        login: secret.login.clone(),
        consumed_credentials,
    })
}

pub(crate) fn updated_binding_credentials(binding: &str, account: &str) -> Result<String> {
    let mut binding = serde_json::from_str::<BindingSecrets>(binding)?;
    let account: AccountSecrets = serde_json::from_str(account)?;
    if binding.connection.origin != account.origin
        || binding.connection.account_id != account.account_id
        || binding.installation_id != account.installation_id
        || binding.control_private != account.control_private
        || account.device_token.is_empty()
    {
        return Err(invalid());
    }
    binding.device_token = account.device_token.clone();
    Ok(serde_json::to_string(&binding)?)
}

pub(crate) fn signed_out_credentials(raw: &str, origin: &str, account: &str) -> Result<String> {
    checked(cloud_crypto::exact_origin(origin))?;
    let mut secret: AccountSecrets = serde_json::from_str(raw)?;
    if secret.origin != origin || secret.account_id != account {
        return Err(invalid());
    }
    secret.access_token.zeroize();
    secret.access_token.clear();
    secret.refresh_token.zeroize();
    secret.refresh_token.clear();
    secret.refresh_request_id.clear();
    secret.device_token.zeroize();
    secret.device_token.clear();
    secret.access_deadline = 0;
    // Retain the installation/key identity so an explicit later login can
    // restore existing pins, without retaining any usable cloud session.
    Ok(serde_json::to_string(&secret)?)
}

pub(crate) async fn account_logout(raw: &str, origin: &str, account: &str) -> Result<()> {
    let secret: AccountSecrets = serde_json::from_str(raw)?;
    if secret.origin != origin || secret.account_id != account {
        return Err(invalid());
    }
    api(
        origin,
        "/v1/auth/logout",
        Method::POST,
        Some(json!({})),
        &secret.access_token,
    )
    .await?;
    Ok(())
}

pub(crate) async fn refresh(raw: String, origin: String, account_id: String) -> Result<String> {
    let mut secret: AccountSecrets = serde_json::from_str(&raw)?;
    if secret.origin != origin || secret.account_id != account_id || secret.refresh_token.is_empty()
    {
        return Err(invalid());
    }
    if secret.refresh_request_id.is_empty() {
        return Err(invalid());
    }
    let started = now();
    let v = api(
        &origin,
        "/v1/auth/refresh",
        Method::POST,
        Some(json!({"refresh_token":secret.refresh_token,"request_id":secret.refresh_request_id})),
        "",
    )
    .await?;
    if v["account"]["id"] != secret.account_id {
        return Err(invalid());
    }
    secret.access_token = v["access_token"].as_str().ok_or_else(invalid)?.into();
    secret.refresh_token = v["refresh_token"].as_str().ok_or_else(invalid)?.into();
    secret.refresh_request_id.clear();
    secret.access_deadline = started + v["expires_in"].as_i64().ok_or_else(invalid)?;
    Ok(serde_json::to_string(&secret)?)
}

pub(crate) async fn entitlement(
    raw: &str,
    origin: &str,
    account: &str,
) -> Result<CloudEntitlement> {
    let secret: AccountSecrets = serde_json::from_str(raw)?;
    if secret.origin != origin || secret.account_id != account {
        return Err(invalid());
    }
    let v = api(
        origin,
        "/v1/account/entitlement",
        Method::GET,
        None,
        &secret.access_token,
    )
    .await?;
    Ok(CloudEntitlement {
        active: v["active"] == true,
        required: v["required"] == true,
        expires_at: v["expires_at"].as_str().map(str::to_owned),
    })
}

fn new_binding(
    gateway: CloudGateway,
    binding_id: String,
    raw: &str,
    device_name: String,
) -> Result<RelayEnrollment> {
    let account: AccountSecrets = serde_json::from_str(raw)?;
    if account.origin != gateway.origin
        || account.device_token.len() != 43
        || device_name.is_empty()
        || device_name.len() > 100
        || device_name.chars().any(char::is_control)
    {
        return Err(invalid());
    }
    checked(cloud_crypto::exact_origin(&account.origin))?;
    id(&gateway.id)?;
    checked(cloud_crypto::key(&gateway.control_public_key))?;
    let private = checked(cloud_crypto::key(&account.control_private))?;
    let control_public = checked(cloud_crypto::public_key(&private))?;
    let preview = checked(cloud_crypto::new_keypair())?;
    let name = gateway.name.clone();
    let connection = RelayConnection {
        origin: account.origin.clone(),
        account_id: account.account_id.clone(),
        gateway_id: gateway.id,
        binding_id: binding_id.clone(),
        desktop_public_key: gateway.control_public_key,
    };
    let secret = BindingSecrets {
        connection: connection.clone(),
        installation_id: account.installation_id.clone(),
        device_token: account.device_token.clone(),
        control_private: account.control_private.clone(),
        control_public: b64(&control_public),
        preview_private: b64(&preview.private),
        preview_public: b64(&preview.public),
        desktop_preview_public: String::new(),
        device_name,
        pairing_code: String::new(),
    };
    Ok(RelayEnrollment {
        computer: Computer {
            id: binding_id,
            name,
            base_url: account.origin.clone(),
            connection_type: ConnectionType::Relay,
            relay: Some(connection),
        },
        account_credentials: serde_json::to_string(&account)?,
        binding_credentials: serde_json::to_string(&secret)?,
        mobile_control_public_key: secret.control_public.clone(),
        mobile_preview_public_key: secret.preview_public.clone(),
    })
}

pub(crate) async fn devices(
    raw: &str,
    origin: &str,
    account_id: &str,
) -> Result<Vec<CloudDiscoveredDevice>> {
    let account: AccountSecrets = serde_json::from_str(raw)?;
    if account.origin != origin
        || account.account_id != account_id
        || account.device_token.len() != 43
    {
        return Err(invalid());
    }
    if !capabilities(origin).await?.automatic_discovery {
        return Err(BridgeError::Protocol(
            "Automatic device discovery requires matching server, desktop and phone versions"
                .into(),
        ));
    }
    let public = b64(&checked(cloud_crypto::public_key(&checked(
        cloud_crypto::key(&account.control_private),
    )?))?);
    let response = api(
        origin,
        "/v1/mobile/bindings",
        Method::GET,
        None,
        &account.device_token,
    )
    .await?;
    let values = response["bindings"]
        .as_array()
        .filter(|v| v.len() <= 200)
        .ok_or_else(invalid)?;
    let mut seen = std::collections::HashSet::new();
    let mut result = Vec::new();
    for value in values {
        let binding = value["id"].as_str().ok_or_else(invalid)?;
        let gateway = value["gateway_id"].as_str().ok_or_else(invalid)?;
        id(binding)?;
        id(gateway)?;
        if !seen.insert(gateway.to_owned())
            || value["installation_id"] != account.installation_id
            || value["mobile_public_key"] != public
        {
            return Err(invalid());
        }
        let key = value["gateway_public_key"].as_str().ok_or_else(invalid)?;
        checked(cloud_crypto::key(key))?;
        let name = value["gateway_name"]
            .as_str()
            .filter(|v| !v.is_empty() && v.len() <= 100 && !v.chars().any(char::is_control))
            .ok_or_else(invalid)?;
        let status: CloudDeviceStatus = serde_json::from_value(value["status"].clone())?;
        let revision = value["access_revision"]
            .as_i64()
            .filter(|v| *v >= 0)
            .ok_or_else(invalid)?;
        result.push(CloudDiscoveredDevice {
            gateway: CloudGateway {
                origin: origin.into(),
                id: gateway.into(),
                name: name.into(),
                control_public_key: key.into(),
                online: value["gateway_online"] == true && value["device_revoked"] != true,
            },
            binding_id: binding.into(),
            status: if value["device_revoked"] == true {
                CloudDeviceStatus::Revoked
            } else {
                status
            },
            access_revision: revision,
        });
    }
    Ok(result)
}

pub(crate) fn prepare_discovery(
    device: CloudDiscoveredDevice,
    raw: &str,
    device_name: String,
) -> Result<RelayEnrollment> {
    id(&device.binding_id)?;
    if device.status == CloudDeviceStatus::Revoked {
        return Err(invalid());
    }
    new_binding(device.gateway, device.binding_id, raw, device_name)
}

type Socket = WebSocketStream<MaybeTlsStream<TcpStream>>;
struct Channel {
    socket: Socket,
    cipher: ControlCipher,
    binding: Uuid,
    channel: Uuid,
}
impl Channel {
    async fn send_data(&mut self, data: &[u8]) -> Result<()> {
        let payload = checked(self.cipher.encrypt(data))?;
        send_frame(
            &mut self.socket,
            &Frame {
                kind: 2,
                binding: self.binding,
                channel: self.channel,
                payload,
            },
        )
        .await
    }
    async fn send(&mut self, value: Value) -> Result<()> {
        let mut raw = vec![0];
        raw.extend(serde_json::to_vec(&value)?);
        self.send_data(&raw).await
    }
    async fn receive(&mut self, timeout: u64) -> Result<Vec<u8>> {
        let frame = read_frame(&mut self.socket, timeout).await?;
        if frame.kind != 2 || frame.binding != self.binding || frame.channel != self.channel {
            return Err(invalid());
        }
        checked(self.cipher.decrypt(&frame.payload))
    }
    async fn control(&mut self, timeout: u64) -> Result<Value> {
        let data = self.receive(timeout).await?;
        if data.first() != Some(&0) {
            return Err(invalid());
        }
        Ok(serde_json::from_slice(&data[1..])?)
    }
}

async fn send_frame(socket: &mut Socket, frame: &Frame) -> Result<()> {
    let raw = checked(frame.encode())?;
    tokio::time::timeout(
        Duration::from_secs(5),
        socket.send(Message::Binary(raw.into())),
    )
    .await
    .map_err(|_| BridgeError::Network)?
    .map_err(|_| BridgeError::Network)
}
async fn read_frame(socket: &mut Socket, timeout: u64) -> Result<Frame> {
    tokio::time::timeout(Duration::from_secs(timeout), async {
        loop {
            match socket.next().await {
                Some(Ok(Message::Binary(raw))) => return checked(Frame::decode(&raw)),
                Some(Ok(Message::Ping(data))) => socket
                    .send(Message::Pong(data))
                    .await
                    .map_err(|_| BridgeError::Network)?,
                Some(Ok(Message::Pong(_))) => {}
                _ => return Err(BridgeError::Network),
            }
        }
    })
    .await
    .map_err(|_| BridgeError::Network)?
}

pub(crate) struct Relay {
    secret: RwLock<BindingSecrets>,
    discovery_cancel: RwLock<CancellationToken>,
    session: tokio::sync::Mutex<SessionCache>,
    status: tokio::sync::watch::Sender<String>,
    connect_cancel: RwLock<CancellationToken>,
    observer_cancel: RwLock<CancellationToken>,
    generation: std::sync::atomic::AtomicU64,
}
#[derive(Default)]
struct SessionCache {
    active: Option<Arc<session::Session>>,
    failures: u32,
    next: Option<tokio::time::Instant>,
}
impl Relay {
    pub(crate) fn new(connection: &RelayConnection, saved: &str) -> Result<Self> {
        checked(cloud_crypto::exact_origin(&connection.origin))?;
        for value in [
            &connection.account_id,
            &connection.gateway_id,
            &connection.binding_id,
        ] {
            id(value)?;
        }
        let mut secret = serde_json::from_str::<BindingSecrets>(saved)?;
        if &secret.connection != connection || secret.device_token.len() != 43 {
            return Err(invalid());
        }
        let private = checked(cloud_crypto::key(&secret.control_private))?;
        if b64(&checked(cloud_crypto::public_key(&private))?) != secret.control_public {
            return Err(invalid());
        }
        let preview = checked(cloud_crypto::key(&secret.preview_private))?;
        if b64(&checked(cloud_crypto::public_key(&preview))?) != secret.preview_public {
            return Err(invalid());
        }
        if secret.device_name.is_empty() {
            secret.device_name = "Phone".into();
        }
        Ok(Self {
            secret: RwLock::new(secret),
            discovery_cancel: RwLock::new(CancellationToken::new()),
            session: tokio::sync::Mutex::new(SessionCache::default()),
            status: tokio::sync::watch::channel("disconnected".into()).0,
            connect_cancel: RwLock::new(CancellationToken::new()),
            observer_cancel: RwLock::new(CancellationToken::new()),
            generation: std::sync::atomic::AtomicU64::new(0),
        })
    }
    pub(crate) fn export(&self) -> Result<String> {
        Ok(serde_json::to_string(&*self.secret.read().unwrap())?)
    }
    pub(crate) fn saved_safety_code(&self) -> Result<String> {
        let s = self.secret.read().unwrap();
        if s.pairing_code.is_empty() {
            return Ok(String::new());
        }
        let private = zeroize::Zeroizing::new(checked(cloud_crypto::key(&s.control_private))?);
        let desktop = checked(cloud_crypto::key(&s.connection.desktop_public_key))?;
        let code = checked(cloud_crypto::pairing_safety_code(
            &s.connection.origin,
            id(&s.connection.gateway_id)?,
            id(&s.connection.binding_id)?,
            private.as_ref(),
            &desktop,
            &desktop,
            &checked(cloud_crypto::key(&s.control_public))?,
            &checked(cloud_crypto::key(&s.desktop_preview_public))?,
            &checked(cloud_crypto::key(&s.preview_public))?,
        ))?;
        if code != s.pairing_code {
            return Err(invalid());
        }
        Ok(code)
    }
    fn check_binding(&self, record: &Value) -> Result<String> {
        let s = self.secret.read().unwrap();
        if record["id"] != s.connection.binding_id
            || record["gateway_id"] != s.connection.gateway_id
            || record["installation_id"] != s.installation_id
            || record["gateway_public_key"] != s.connection.desktop_public_key
            || record["mobile_public_key"] != s.control_public
        {
            return Err(invalid());
        }
        record["status"]
            .as_str()
            .filter(|v| matches!(*v, "pending" | "active" | "disabled" | "revoked"))
            .map(str::to_owned)
            .ok_or_else(invalid)
    }
    pub(crate) async fn pairing_state(&self) -> Result<RelayPairingState> {
        let cancel = self.discovery_cancel.read().unwrap().clone();
        tokio::select! {
            _ = cancel.cancelled() => Err(BridgeError::Network),
            result = self.read_pairing_state() => result,
        }
    }
    pub(crate) fn cancel_discovery(&self) {
        let mut cancel = self.discovery_cancel.write().unwrap();
        cancel.cancel();
        *cancel = CancellationToken::new();
    }
    async fn read_pairing_state(&self) -> Result<RelayPairingState> {
        let s = self.secret.read().unwrap().clone();
        let value = api(
            &s.connection.origin,
            &format!("/v1/bindings/{}", s.connection.binding_id),
            Method::GET,
            None,
            &s.device_token,
        )
        .await?;
        let mut status = self.check_binding(&value)?;
        if value["device_revoked"] == true {
            status = "revoked".into();
        }
        if matches!(status.as_str(), "pending" | "active") && value["gateway_online"] == true {
            if status == "active"
                && !s.pairing_code.is_empty()
                && !s.desktop_preview_public.is_empty()
            {
                self.session().await?;
            } else if s.pairing_code.is_empty() || s.desktop_preview_public.is_empty() {
                let paired = self.connect(true).await?;
                if paired.is_none() {
                    status = "pending".into();
                }
            }
        }
        Ok(RelayPairingState {
            status,
            safety_code: self.secret.read().unwrap().pairing_code.clone(),
        })
    }
    async fn connect(&self, pairing: bool) -> Result<Option<Channel>> {
        let s = self.secret.read().unwrap().clone();
        let url = websocket_url(&s.connection.origin)?;
        let mut request = url.into_client_request().map_err(|_| invalid())?;
        let headers = request.headers_mut();
        headers.insert(
            "Authorization",
            format!("Bearer {}", s.device_token)
                .parse()
                .map_err(|_| invalid())?,
        );
        headers.insert(
            "X-Bridge-Binding",
            s.connection.binding_id.parse().map_err(|_| invalid())?,
        );
        headers.insert("Sec-WebSocket-Protocol", "bridge-relay-v2".parse().unwrap());
        let config = WebSocketConfig::default()
            .read_buffer_size(32768)
            .write_buffer_size(0)
            .max_write_buffer_size(2 * (MAX_CHUNK + 50))
            .max_message_size(Some(MAX_CHUNK + 50))
            .max_frame_size(Some(MAX_CHUNK + 50));
        let (mut socket, response) = tls::connect(request, config).await?;
        if response
            .headers()
            .get("Sec-WebSocket-Protocol")
            .and_then(|v| v.to_str().ok())
            != Some("bridge-relay-v2")
        {
            return Err(invalid());
        }
        let open = read_frame(&mut socket, 15).await?;
        let desktop = checked(cloud_crypto::key(&s.connection.desktop_public_key))?;
        if open.kind != 1
            || open.binding != id(&s.connection.binding_id)?
            || open.payload != desktop
        {
            return Err(invalid());
        }
        let mobile = checked(cloud_crypto::key(&s.control_public))?;
        let private = zeroize::Zeroizing::new(checked(cloud_crypto::key(&s.control_private))?);
        let p = checked(cloud_crypto::control_prologue(
            &s.connection.origin,
            id(&s.connection.gateway_id)?,
            open.binding,
            open.channel,
            &desktop,
            &mobile,
        ))?;
        let mut handshake = checked(cloud_crypto::handshake(
            private.as_ref(),
            &desktop,
            &p,
            true,
        ))?;
        let first = checked(cloud_crypto::handshake_write(&mut handshake))?;
        send_frame(
            &mut socket,
            &Frame {
                kind: 2,
                binding: open.binding,
                channel: open.channel,
                payload: first,
            },
        )
        .await?;
        let second = read_frame(&mut socket, 15).await?;
        if second.kind != 2 || second.binding != open.binding || second.channel != open.channel {
            return Err(invalid());
        }
        checked(cloud_crypto::handshake_read(
            &mut handshake,
            &second.payload,
        ))?;
        let mut channel = Channel {
            socket,
            cipher: checked(ControlCipher::new(handshake))?,
            binding: open.binding,
            channel: open.channel,
        };
        if channel.control(15).await? != json!({"kind":"ready"}) {
            return Err(invalid());
        }
        channel.send(json!({"kind":"key_confirm"})).await?;
        if channel.control(15).await? != json!({"kind":"key_confirmed"}) {
            return Err(invalid());
        }
        channel.send(if pairing {
            json!({"kind":"pair","request_id":s.connection.binding_id,"device_name":s.device_name,"preview_public_key":s.preview_public})
        } else { json!({"kind":"authorize","preview_public_key":s.preview_public}) }).await?;
        let authorized = channel.control(15).await?;
        let preview = authorized["desktop_preview_public_key"]
            .as_str()
            .ok_or_else(invalid)?;
        let desktop_preview = checked(cloud_crypto::key(preview))?;
        if !s.desktop_preview_public.is_empty() && s.desktop_preview_public != preview {
            return Err(invalid());
        }
        if pairing {
            let code = checked(cloud_crypto::pairing_safety_code(
                &s.connection.origin,
                id(&s.connection.gateway_id)?,
                open.binding,
                private.as_ref(),
                &desktop,
                &desktop,
                &mobile,
                &desktop_preview,
                &checked(cloud_crypto::key(&s.preview_public))?,
            ))?;
            if !s.pairing_code.is_empty() && s.pairing_code != code {
                return Err(invalid());
            }
            if authorized["kind"] == "pair_pending" && authorized["safety_code"] != code {
                return Err(invalid());
            }
            {
                let mut saved = self.secret.write().unwrap();
                saved.desktop_preview_public = preview.into();
                saved.pairing_code = code.clone();
            }
            if authorized["kind"] == "pair_pending" {
                if authorized["safety_code"] != code {
                    return Err(invalid());
                }
                let _ = channel.socket.close(None).await;
                return Ok(None);
            }
        }
        if authorized["kind"] != "authorized" || (!pairing && s.desktop_preview_public.is_empty()) {
            return Err(invalid());
        }
        Ok(Some(channel))
    }
    pub(crate) fn connection_status(&self) -> tokio::sync::watch::Receiver<String> {
        self.status.subscribe()
    }
    pub(crate) fn connected_generation(&self) -> Option<u64> {
        (self.status.borrow().as_str() == "connected")
            .then(|| self.generation.load(std::sync::atomic::Ordering::Acquire))
    }
    pub(crate) async fn ensure_session(&self) -> Result<u64> {
        self.session().await?;
        self.connected_generation().ok_or(BridgeError::Network)
    }
    pub(crate) fn connection_observer(&self) -> CancellationToken {
        let mut token = self.observer_cancel.write().unwrap();
        token.cancel();
        *token = CancellationToken::new();
        let paused = self.status.borrow().as_str() == "paused";
        if paused {
            self.status.send_replace("disconnected".into());
        }
        token.clone()
    }
    pub(crate) fn cancel_connection_observer(&self) {
        self.observer_cancel.read().unwrap().cancel();
    }
    pub(crate) async fn pause(&self) {
        self.cancel_connection_observer();
        {
            let mut token = self.connect_cancel.write().unwrap();
            token.cancel();
            *token = CancellationToken::new();
        }
        let mut cache = self.session.lock().await;
        if let Some(active) = &cache.active {
            active.quiesce.cancel();
        }
        cache.next = None;
        self.status.send_replace("paused".into());
    }
    pub(crate) async fn revoke(&self) {
        self.pause().await;
        let mut cache = self.session.lock().await;
        if let Some(active) = cache.active.take() {
            active.closed.cancel();
        }
    }
    async fn session(&self) -> Result<Arc<session::Session>> {
        let cancelled = self.connect_cancel.read().unwrap().clone();
        // All callers share one handshake/backoff; business requests are never replayed.
        let mut cache = tokio::select! { _ = cancelled.cancelled() => return Err(BridgeError::Cancelled), value=self.session.lock()=>value };
        if let Some(active) = &cache.active
            && !active.closed.is_cancelled()
        {
            if active.quiesce.is_cancelled() {
                tokio::select! {_=cancelled.cancelled()=>return Err(BridgeError::Cancelled),_=active.closed.cancelled()=>{}}
            } else {
                return Ok(active.clone());
            }
        }
        if let Some(next) = cache.next {
            tokio::select! {_=cancelled.cancelled()=>return Err(BridgeError::Cancelled),_=tokio::time::sleep_until(next)=>{}}
        }
        self.status.send_replace("connecting".into());
        let connected = tokio::select! {_=cancelled.cancelled()=>Err(BridgeError::Cancelled), result=self.connect(false)=>result};
        match connected {
            Ok(Some(channel)) => {
                self.generation
                    .fetch_add(1, std::sync::atomic::Ordering::AcqRel);
                let active = session::Session::start(channel, self.status.clone());
                cache.active = Some(active.clone());
                cache.failures = 0;
                cache.next = None;
                self.status.send_replace("connected".into());
                Ok(active)
            }
            result => {
                cache.failures = cache.failures.saturating_add(1);
                let base = (1u64 << cache.failures.saturating_sub(1).min(4)).min(15);
                let jitter = u64::from(Uuid::new_v4().as_bytes()[0]) * 2;
                cache.next = Some(
                    tokio::time::Instant::now()
                        + Duration::from_millis((base * 1000 + jitter).min(15_000)),
                );
                let error = result.err().unwrap_or_else(invalid);
                let state = match &error {
                    BridgeError::Relay(code) => code.as_str(),
                    BridgeError::Tls => "certificate",
                    BridgeError::Cancelled => "paused",
                    _ => "reconnecting",
                };
                self.status.send_replace(state.into());
                Err(error)
            }
        }
    }
    pub(crate) async fn request(
        &self,
        method: Method,
        path: &str,
        body: Option<Vec<u8>>,
        content_type: &str,
        headers: serde_json::Map<String, serde_json::Value>,
    ) -> Result<Response> {
        if !matches!(method, Method::GET | Method::POST) {
            return Err(invalid());
        }
        let body = if method == Method::GET {
            Vec::new()
        } else {
            body.unwrap_or_else(|| b"{}".to_vec())
        };
        if body.len() > MAX_FILE {
            return Err(BridgeError::TooLarge);
        }
        let mut channel = self.session().await?.open(method != Method::GET).await?;
        if body.len() > 1024 * 1024
            || ["/files/", "/upload", "/download"]
                .iter()
                .any(|part| path.contains(part))
        {
            channel.large()?;
        }
        channel.send(json!({"kind":"request","id":Uuid::new_v4(),"method":method.as_str(),"path":path,
            "content_type":if content_type.is_empty() { "application/json" } else { content_type },"headers":headers,"length":body.len()})).await?;
        let mut credits = credit(&channel.control(15).await?)?;
        for chunk in body.chunks(MAX_CHUNK - 9) {
            if credits == 0 {
                credits = credit(&channel.control(15).await?)?;
            }
            credits -= 1;
            channel.send_body(chunk).await?;
        }
        channel.send(json!({"kind":"request_end"})).await?;
        let start = loop {
            let value = channel.control(80).await?;
            if value["kind"] == "credit" {
                credit(&value)?;
                continue;
            }
            break value;
        };
        if start["kind"] != "response_start" {
            return Err(invalid());
        }
        let status = start["status"]
            .as_u64()
            .filter(|v| (100..600).contains(v))
            .ok_or_else(invalid)? as u16;
        if (300..400).contains(&status) {
            return Err(BridgeError::Redirect);
        }
        let headers = start["headers"].as_object().ok_or_else(invalid)?;
        let mut response = http::Response::builder().status(status);
        let mut sse = false;
        for (key, value) in headers {
            if ![
                "Content-Type",
                "Content-Length",
                "ETag",
                "Content-Range",
                "Accept-Ranges",
                "Content-Disposition",
            ]
            .contains(&key.as_str())
            {
                return Err(invalid());
            }
            let text = value
                .as_str()
                .filter(|v| v.len() <= if key == "Content-Disposition" { 4096 } else { 256 }
                    && v.bytes().all(|byte| (32..=126).contains(&byte)))
                .ok_or_else(invalid)?;
            if key == "Content-Length"
                && text.parse::<u64>().map_err(|_| invalid())? > MAX_FILE as u64
            {
                return Err(BridgeError::TooLarge);
            }
            if key == "Content-Length" && text.parse::<u64>().map_err(|_| invalid())? > 1024 * 1024
            {
                channel.large()?;
            }
            if key == "Content-Type" {
                sse = text.starts_with("text/event-stream");
            }
            response = response.header(key, text);
        }
        let (tx, rx) =
            tokio::sync::mpsc::channel::<std::result::Result<Vec<u8>, std::io::Error>>(4);
        channel.send(json!({"kind":"credit","count":4})).await?;
        tokio::spawn(receive_response_body(channel, tx, sse));
        let stream = futures_util::stream::unfold(rx, |mut rx| async move {
            rx.recv().await.map(|part| (part, rx))
        });
        Ok(response
            .body(reqwest::Body::wrap_stream(stream))
            .map_err(|_| invalid())?
            .into())
    }

}

async fn receive_response_body(
    mut channel: session::Substream,
    tx: tokio::sync::mpsc::Sender<std::result::Result<Vec<u8>, std::io::Error>>,
    sse: bool,
) {
    let mut total = 0usize;
    let mut credit_writable = true;
    let result: Result<()> = async {
        loop {
            let data =
                tokio::select! { _ = tx.closed() => return Ok(()), v = channel.receive(80) => v? };
            if data.first() == Some(&1) {
                total = total.saturating_add(data.len() - 1);
                if !sse && total > MAX_FILE {
                    return Err(BridgeError::TooLarge);
                }
                if tx.send(Ok(data[1..].to_vec())).await.is_err() {
                    return Ok(());
                }
                if credit_writable {
                    // The desktop can finish and close after sending its last
                    // window. A failed trailing credit must not discard the
                    // authenticated response_end already queued for reading.
                    credit_writable = channel
                        .send(json!({"kind":"credit","count":1}))
                        .await
                        .is_ok();
                }
            } else if data.first() == Some(&0)
                && serde_json::from_slice::<Value>(&data[1..])? == json!({"kind":"response_end"})
            {
                channel.finish().await;
                return Ok(());
            } else {
                return Err(invalid());
            }
        }
    }
    .await;
    if result.is_err() {
        let _ = tx
            .send(Err(std::io::Error::other("encrypted relay disconnected")))
            .await;
    }
}

impl Drop for Relay {
    fn drop(&mut self) {
        if let Some(active) = self.session.get_mut().active.take() {
            active.closed.cancel();
        }
    }
}



fn credit(value: &Value) -> Result<usize> {
    if value["kind"] != "credit" {
        return Err(invalid());
    }
    value["count"]
        .as_u64()
        .filter(|v| (1..=4).contains(v))
        .map(|v| v as usize)
        .ok_or_else(invalid)
}
