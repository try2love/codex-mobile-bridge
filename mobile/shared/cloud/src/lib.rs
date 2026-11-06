//! Native-only cloud account and transport boundary. Web content cannot invoke this interface.
mod api {
    pub(crate) mod types {
        include!("types.rs");
    }
}
mod cloud_crypto;
mod error;
mod origin;
mod relay;
mod transport;
mod web;

use anyhow::{Context, Result, bail, ensure};
use api::types::*;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    ffi::{CStr, CString, c_char, c_void},
    sync::{Arc, OnceLock, mpsc},
};
use tokio::runtime::Runtime;

#[derive(Default, Clone, Serialize, Deserialize)]
struct Vault {
    origin: String,
    account: String,
    credentials: String,
    bindings: BTreeMap<String, (RelayConnection, String)>,
    #[serde(default)]
    login: String,
    #[serde(default)]
    signed_out: bool,
    #[serde(default)]
    aliases: BTreeMap<String, String>,
    #[serde(default)]
    known: BTreeMap<String, String>,
}
impl Vault {
    async fn renew(&mut self, save: &mut impl FnMut(&Self) -> Result<()>) -> Result<()> {
        ensure!(!self.credentials.is_empty(), "请先登录云账号");
        let state = relay::credential_state(&self.credentials, &self.origin, &self.account)?;
        if let Some(consumed) = state.consumed_credentials {
            self.credentials = consumed;
            save(self)?; // Same refresh request ID on retry, including after a crash.
            self.credentials = relay::refresh(
                self.credentials.clone(),
                self.origin.clone(),
                self.account.clone(),
            )
            .await?;
            save(self)?;
        }
        Ok(())
    }
    fn device(&self, id: &str) -> Result<relay::Relay> {
        let (connection, saved) = self.bindings.get(id).context("云设备尚未登记")?;
        ensure!(
            connection.origin == self.origin
                && connection.account_id == self.account
                && connection.binding_id == id,
            "云设备身份不匹配"
        );
        let updated = relay::updated_binding_credentials(saved, &self.credentials)?;
        Ok(relay::Relay::new(connection, &updated)?)
    }
}

const OFFICIAL: &str = "https://bridge.waken.cloudns.be:8787";
fn field<'a>(input: &'a Value, name: &str) -> Result<&'a str> {
    input
        .get(name)
        .and_then(Value::as_str)
        .context("请求字段无效")
}
fn login_origin(input: &Value) -> Result<&str> {
    match field(input, "serverType")? {
        "official" => {
            ensure!(input.get("origin").is_none(), "官方云服务无需填写地址");
            Ok(OFFICIAL)
        }
        "custom" => {
            let value = field(input, "origin")?;
            ensure!(value.starts_with("https://"), "请输入自建服务的 HTTPS 地址");
            origin::exact_origin(value)
                .context("请输入完整的 HTTPS 源地址，不含路径、查询或片段")?;
            Ok(value)
        }
        _ => bail!("请选择云服务类型"),
    }
}
#[derive(Default, Serialize, Deserialize)]
struct Profiles {
    profiles: BTreeMap<String, Vault>,
}

impl Profiles {
    fn decode(raw: &[u8]) -> Result<Self> {
        if raw.is_empty() {
            return Ok(Self::default());
        }
        let value: Value = serde_json::from_slice(raw).context("云登录存储损坏")?;
        if value.get("profiles").is_some() {
            return Ok(serde_json::from_value(value)?);
        }
        let mut profile: Vault = serde_json::from_value(value)?;
        let mut store = Self::default();
        if !profile.credentials.is_empty() {
            profile.login = serde_json::from_str::<Value>(&profile.credentials)?["login"]
                .as_str()
                .unwrap_or("")
                .to_owned();
            store
                .profiles
                .insert(profile_id(&profile.origin, &profile.account), profile);
        }
        Ok(store)
    }
    fn list(&self) -> Vec<Value> {
        self.profiles.iter().map(|(id,p)|json!({"id":id,"label":p.label(),"login":p.login,
            "serverType":if p.origin==OFFICIAL{"official"}else{"custom"},
            "customOrigin":if p.origin==OFFICIAL{None}else{Some(&p.origin)},"signedOut":p.signed_out,
            "computers":p.known.iter().map(|(key,name)|p.aliases.get(key).unwrap_or(name)).collect::<Vec<_>>()})).collect()
    }
}
fn profile_id(origin: &str, account: &str) -> String {
    use sha2::{Digest, Sha256};
    format!(
        "{:x}",
        Sha256::digest(format!("{origin}\0{account}").as_bytes())
    )
}
impl Vault {
    fn label(&self) -> String {
        let server = if self.origin == OFFICIAL {
            "官方云服务".into()
        } else {
            self.origin.trim_start_matches("https://").to_owned()
        };
        format!("{} · {}", server, self.login)
    }
}
async fn action(
    input: Value,
    mut store: Profiles,
    save: impl Fn(&Profiles) -> Result<()> + Sync,
) -> Result<Value> {
    let op = field(&input, "op")?;
    if op == "close" {
        web::close().await;
        return Ok(json!({}));
    }
    if op == "profiles" {
        return Ok(json!({"profiles":store.list()}));
    }
    if op == "login" {
        let origin = login_origin(&input)?;
        let existing = store
            .profiles
            .values()
            .filter(|p| p.origin == origin)
            .map(|p| p.credentials.clone())
            .collect();
        let login = relay::login(
            origin.into(),
            field(&input, "login")?.into(),
            field(&input, "password")?.into(),
            existing,
        )
        .await?;
        let id = profile_id(origin, &login.account_id);
        let p = store.profiles.entry(id.clone()).or_default();
        p.origin = origin.into();
        p.account = login.account_id;
        p.login = login.login;
        p.credentials = login.credentials;
        p.signed_out = false;
        save(&store)?;
        return Ok(json!({"profile":id}));
    }
    if op == "devices" {
        use futures_util::{StreamExt, stream};
        use std::sync::Mutex;
        let name = input["name"].as_str().unwrap_or("Bridge Mobile");
        let selected: Vec<_> = store
            .profiles
            .iter()
            .filter(|(_, p)| !p.signed_out)
            .map(|(id, p)| (id.clone(), p.clone()))
            .collect();
        let shared = Mutex::new(store);
        let results: Vec<_> = stream::iter(selected)
            .map(|(id, p)| {
                let shared = &shared;
                let save = &save;
                async move {
                    let label = p.label();
                    let result = device_rows(&id, p, name, |p| {
                        let mut all = shared.lock().unwrap();
                        all.profiles.insert(id.clone(), p.clone());
                        save(&all)
                    })
                    .await;
                    (label, result)
                }
            })
            .buffer_unordered(3)
            .collect()
            .await;
        let mut rows = Vec::new();
        let mut errors = Vec::new();
        for (label, result) in results {
            match result {
                Ok(devices) => rows.extend(devices),
                Err(e) => errors.push(format!("{label}: {e}")),
            }
        }
        rows.sort_by_key(|v| {
            (
                v["source"].as_str().unwrap_or("").to_string(),
                v["name"].as_str().unwrap_or("").to_string(),
            )
        });
        return Ok(json!({"devices":rows,"errors":errors}));
    }
    let id = field(&input, "profile")?.to_owned();
    let mut vault = store
        .profiles
        .get(&id)
        .context("云配置已删除，请刷新")?
        .clone();
    match op {
        "rename" => {
            let gateway = field(&input, "gateway")?;
            ensure!(vault.known.contains_key(gateway), "电脑不属于此云配置");
            let name = field(&input, "name")?.trim();
            ensure!(
                name.chars().count() <= 80 && !name.chars().any(char::is_control),
                "电脑名称无效"
            );
            if name.is_empty() {
                vault.aliases.remove(gateway);
            } else {
                vault.aliases.insert(gateway.into(), name.into());
            }
            let display_name = vault
                .aliases
                .get(gateway)
                .unwrap_or(&vault.known[gateway])
                .clone();
            store.profiles.insert(id, vault);
            save(&store)?;
            return Ok(json!({"name":display_name}));
        }
        "logout" | "remove" => {
            web::close_profile(&id).await;
            let remote = (!vault.signed_out).then(|| vault.credentials.clone());
            let origin = vault.origin.clone();
            let account = vault.account.clone();
            if op == "remove" {
                store.profiles.remove(&id);
            } else {
                vault.credentials =
                    relay::signed_out_credentials(&vault.credentials, &origin, &account)?;
                vault.signed_out = true;
                for (_, encoded) in vault.bindings.values_mut() {
                    let mut secret: Value = serde_json::from_str(encoded)?;
                    secret["device_token"] = json!("");
                    *encoded = secret.to_string();
                }
                store.profiles.insert(id, vault);
            }
            save(&store)?;
            let confirmed = if let Some(raw) = remote {
                relay::account_logout(&raw, &origin, &account).await.is_ok()
            } else {
                true
            };
            return Ok(json!({"profiles":store.list(),"remoteConfirmed":confirmed}));
        }
        "open" | "verify" => {}
        _ => bail!("未知的云操作"),
    }
    ensure!(!vault.signed_out, "请先登录此云配置");
    let mut checkpoint = |p: &Vault| {
        store.profiles.insert(id.clone(), p.clone());
        save(&store)
    };
    vault.renew(&mut checkpoint).await?;
    let binding = field(&input, "binding")?;
    let devices = relay::devices(&vault.credentials, &vault.origin, &vault.account).await?;
    let item = devices
        .into_iter()
        .find(|d| d.binding_id == binding)
        .context("同账号设备列表中找不到此电脑")?;
    let (client, state) =
        verified_device(&mut vault, item, field(&input, "name")?, &mut checkpoint).await?;
    if op == "verify" || state.status != "active" {
        return Ok(json!({"status":state.status,"safety":state.safety_code}));
    }
    client.ensure_session().await?;
    Ok(json!({"status":"active","entry":web::open(id,client).await?,"safety":state.safety_code}))
}

async fn device_rows(
    id: &str,
    mut vault: Vault,
    name: &str,
    mut save: impl FnMut(&Vault) -> Result<()>,
) -> Result<Vec<Value>> {
    vault.renew(&mut save).await?;
    let devices = relay::devices(&vault.credentials, &vault.origin, &vault.account).await?;
    let known = devices
        .iter()
        .map(|d| (d.gateway.id.clone(), d.gateway.name.clone()))
        .collect();
    if vault.known != known {
        vault.known = known;
        save(&vault)?;
    }
    let mut rows = Vec::new();
    for item in devices {
        let mut row = serde_json::to_value(&item)?;
        row["profile"] = json!(id);
        row["source"] = json!(vault.label());
        row["name"] = json!(
            vault
                .aliases
                .get(&item.gateway.id)
                .unwrap_or(&item.gateway.name)
        );
        if item.gateway.online && item.status == CloudDeviceStatus::Pending {
            match verified_device(&mut vault, item, name, &mut save).await {
                Ok((_, state)) => row["safety"] = json!(state.safety_code),
                Err(e) => row["proofError"] = json!(e.to_string()),
            }
        }
        rows.push(row);
    }
    Ok(rows)
}

async fn verified_device(
    vault: &mut Vault,
    item: CloudDiscoveredDevice,
    name: &str,
    save: &mut impl FnMut(&Vault) -> Result<()>,
) -> Result<(Arc<relay::Relay>, RelayPairingState)> {
    let id = item.binding_id.clone();
    ensure!(
        item.status != CloudDeviceStatus::Revoked && item.status != CloudDeviceStatus::Disabled,
        "电脑已关闭手机访问"
    );
    ensure!(item.gateway.online, "电脑离线");
    if let Some((conn, _)) = vault.bindings.get(&id) {
        ensure!(
            conn.gateway_id == item.gateway.id
                && conn.desktop_public_key == item.gateway.control_public_key
                && conn.origin == item.gateway.origin,
            "电脑身份已变化"
        );
    } else {
        let enrolled = relay::prepare_discovery(item, &vault.credentials, name.into())?;
        let conn = enrolled.computer.relay.context("缺少云绑定")?;
        vault
            .bindings
            .insert(id.clone(), (conn, enrolled.binding_credentials));
        save(vault)?;
    }
    let client = Arc::new(vault.device(&id)?);
    let state = client.pairing_state().await?;
    let exported = client.export()?;
    if vault.bindings[&id].1 != exported {
        vault.bindings.get_mut(&id).unwrap().1 = exported;
        save(vault)?;
    }
    Ok((client, state))
}
static RUNTIME: OnceLock<Runtime> = OnceLock::new();
type Save = extern "C" fn(*const c_char, *mut c_void) -> i32;

/// Called only on the mobile controller's serialized background queue.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cmb_cloud_call(
    req: *const c_char,
    state: *const c_char,
    persist: Save,
    context: *mut c_void,
) -> *mut c_char {
    let run = || -> Result<Value> {
        ensure!(!req.is_null() && !state.is_null(), "缺少云命令");
        let input = serde_json::from_slice(unsafe { CStr::from_ptr(req) }.to_bytes())?;
        let raw = unsafe { CStr::from_ptr(state) }.to_bytes();
        let vault = Profiles::decode(raw)?;
        // Network futures, including TLS/Noise handshakes, are large in debug builds.
        // The iOS GCD caller has a 544 KiB stack. Poll only on our runtime; perform
        // vault writes on the caller so Android JNI local references stay thread-bound.
        let rt = RUNTIME.get_or_init(|| {
            tokio::runtime::Builder::new_multi_thread()
                .worker_threads(2)
                .thread_stack_size(4 * 1024 * 1024)
                .enable_all()
                .build()
                .expect("cloud runtime")
        });
        enum Event {
            Save(CString, mpsc::SyncSender<bool>),
            Done(Result<Value>),
        }
        let (events, inbox) = mpsc::channel();
        rt.spawn(async move {
            let result = action(input, vault, |v| {
                let value = CString::new(serde_json::to_vec(v)?)?;
                let (ack, saved) = mpsc::sync_channel(1);
                events
                    .send(Event::Save(value, ack))
                    .map_err(|_| anyhow::anyhow!("云操作已取消"))?;
                ensure!(saved.recv().unwrap_or(false), "无法安全保存云登录");
                Ok(())
            })
            .await;
            let _ = events.send(Event::Done(result));
        });
        loop {
            match inbox.recv().context("云操作中断")? {
                Event::Save(value, ack) => {
                    let _ = ack.send(persist(value.as_ptr(), context) == 1);
                }
                Event::Done(result) => return result,
            }
        }
    };
    let answer = match std::panic::catch_unwind(std::panic::AssertUnwindSafe(run)) {
        Ok(Ok(value)) => json!({"ok":true,"result":value}),
        Ok(Err(e)) => json!({"ok":false,"error":e.to_string()}),
        Err(_) => json!({"ok":false,"error":"云连接中断"}),
    };
    CString::new(answer.to_string()).unwrap().into_raw()
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cmb_cloud_free(value: *mut c_char) {
    if !value.is_null() {
        drop(unsafe { CString::from_raw(value) });
    }
}

#[cfg(target_os = "android")]
mod android_tls;

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn server_type_owns_destination() {
        assert_eq!(
            login_origin(&json!({"serverType":"official"})).unwrap(),
            OFFICIAL
        );
        assert!(
            login_origin(&json!({"serverType":"official","origin":"https://other.test"})).is_err()
        );
        assert_eq!(
            login_origin(&json!({"serverType":"custom","origin":"https://relay.example.test"}))
                .unwrap(),
            "https://relay.example.test"
        );
        for origin in [
            "",
            "http://relay.example.test",
            "https://relay.example.test/path",
            "https://a:b@relay.example.test",
        ] {
            assert!(login_origin(&json!({"serverType":"custom","origin":origin})).is_err());
        }
    }
}

#[cfg(test)]
mod profiles_tests {
    use super::*;

    fn profile(origin: &str, account: &str) -> Vault {
        Vault {
            origin: origin.into(),
            account: account.into(),
            login: "fixture".into(),
            signed_out: true,
            known: BTreeMap::from([("computer".into(), "Desktop".into())]),
            ..Default::default()
        }
    }

    #[tokio::test]
    async fn rename_and_remove_target_only_the_selected_configuration() {
        let mut store = Profiles::default();
        let first = profile_id(OFFICIAL, "same");
        let second = profile_id("https://example.invalid", "same");
        let third = profile_id(OFFICIAL, "different");
        store
            .profiles
            .insert(first.clone(), profile(OFFICIAL, "same"));
        store
            .profiles
            .insert(second.clone(), profile("https://example.invalid", "same"));
        store
            .profiles
            .insert(third.clone(), profile(OFFICIAL, "different"));
        let persisted = std::sync::Mutex::new(Vec::new());
        let save = |s: &Profiles| {
            *persisted.lock().unwrap() = serde_json::to_vec(s)?;
            Ok(())
        };
        action(
            json!({"op":"rename","profile":first,"gateway":"computer","name":"My Mac"}),
            store,
            save,
        )
        .await
        .unwrap();
        let store = Profiles::decode(&persisted.lock().unwrap()).unwrap();
        assert_eq!(store.profiles[&first].aliases["computer"], "My Mac");
        assert!(
            store.profiles[&second].aliases.is_empty() && store.profiles[&third].aliases.is_empty()
        );
        let public = serde_json::to_string(&store.list()).unwrap();
        assert!(!public.contains(OFFICIAL) && !public.contains("credentials"));
        action(json!({"op":"remove","profile":second}), store, save)
            .await
            .unwrap();
        let store = Profiles::decode(&persisted.lock().unwrap()).unwrap();
        assert_eq!(store.profiles.len(), 2);
        assert_eq!(store.profiles[&first].aliases["computer"], "My Mac");
        assert!(store.profiles.contains_key(&third));
    }
    #[test]
    fn migrating_the_single_account_keeps_its_identity_and_credentials() {
        let legacy = json!({"origin":OFFICIAL,"account":"native-id","credentials":"{\"login\":\"fixture\"}","bindings":{}});
        let store = Profiles::decode(&serde_json::to_vec(&legacy).unwrap()).unwrap();
        let original = &store.profiles[&profile_id(OFFICIAL, "native-id")];
        assert_eq!(original.credentials, legacy["credentials"]);
        assert_eq!(original.login, "fixture");
        assert!(!original.signed_out);
    }
}
