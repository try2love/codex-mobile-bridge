//! Device-local drafts and read markers; never sent through the cloud relay.
use crate::api::types::RelayConnection;
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, fs, io::Write, path::{Path, PathBuf}, sync::Mutex};

pub(crate) const MAX_VALUE: usize = 4 * 1024 * 1024;
const MAX_TOTAL: usize = 16 * 1024 * 1024;
const MAX_ITEMS: usize = 256;
fn hash(value: &str) -> String { format!("{:x}", Sha256::digest(value.as_bytes())) }
fn valid_key(key: &str) -> bool {
    if matches!(key, "bridge-session-activity" | "bridge-desktop-session-activity") { return true; }
    if key.len() > 8192 { return false; }
    let Some(rest) = key.strip_prefix("composer-v1:") else { return false; };
    let Some((kind, scope)) = rest.split_once(':') else { return false; };
    matches!(kind, "draft" | "pending" | "pending-v2") &&
        serde_json::from_str::<Vec<String>>(scope).is_ok_and(|parts| parts.len() == 5 && parts.iter().all(|v| !v.is_empty()))
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Change { key: String, value: Option<String> }
pub(crate) struct Store { directory: PathBuf, values: Mutex<BTreeMap<String, String>> }
impl Store {
    pub(crate) fn open(root: &str, profile: &str, peer: &RelayConnection) -> Result<Self> {
        ensure!(Path::new(root).is_absolute(), "Invalid draft directory");
        let directory = Path::new(root).join(hash(profile)).join(hash(&format!("{}\0{}", peer.gateway_id, peer.desktop_public_key)));
        fs::create_dir_all(&directory)?;
        #[cfg(unix)] {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&directory, fs::Permissions::from_mode(0o700))?;
        }
        let mut values = BTreeMap::new();
        let mut size = 0;
        for entry in fs::read_dir(&directory)? {
            let path = entry?.path();
            if path.extension().and_then(|s| s.to_str()) != Some("json") { continue; }
            ensure!(fs::metadata(&path)?.len() <= (MAX_VALUE + 16384) as u64, "Draft too large");
            let Change { key, value } = serde_json::from_slice(&fs::read(path)?)?;
            let value = value.context("Invalid saved draft")?;
            size += value.len();
            ensure!(valid_key(&key) && value.len() <= MAX_VALUE && size <= MAX_TOTAL && values.len() < MAX_ITEMS, "Invalid saved draft");
            values.insert(key, value);
        }
        Ok(Self { directory, values: Mutex::new(values) })
    }
    pub(crate) fn snapshot(&self) -> Result<Value> { Ok(serde_json::to_value(&*self.values.lock().unwrap())?) }
    pub(crate) fn apply(&self, raw: &[u8]) -> Result<Value> {
        let change: Change = serde_json::from_slice(raw)?;
        ensure!(valid_key(&change.key), "Invalid draft key");
        let mut values = self.values.lock().unwrap();
        let file = self.directory.join(hash(&change.key)).with_extension("json");
        if let Some(value) = &change.value {
            if values.get(&change.key) == Some(value) { return Ok(json!({})); }
            let size: usize = values.values().map(String::len).sum();
            ensure!(value.len() <= MAX_VALUE && size - values.get(&change.key).map_or(0, String::len) + value.len() <= MAX_TOTAL && (values.contains_key(&change.key) || values.len() < MAX_ITEMS), "Draft storage full");
            let temp = file.with_extension("tmp");
            let mut options = fs::OpenOptions::new();
            options.write(true).create(true).truncate(true);
            #[cfg(unix)] { use std::os::unix::fs::OpenOptionsExt; options.mode(0o600); }
            let mut out = options.open(&temp)?;
            out.write_all(&serde_json::to_vec(&change)?)?;
            out.sync_all()?;
            fs::rename(temp, file)?;
            values.insert(change.key, value.clone());
        } else {
            match fs::remove_file(file) { Ok(()) => (), Err(e) if e.kind() == std::io::ErrorKind::NotFound => (), Err(e) => return Err(e.into()) }
            values.remove(&change.key);
        }
        Ok(json!({}))
    }
    pub(crate) fn remove_profile(root: &str, profile: &str) -> Result<()> {
        ensure!(Path::new(root).is_absolute(), "Invalid draft directory");
        match fs::remove_dir_all(Path::new(root).join(hash(profile))) {
            Ok(()) => Ok(()), Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()), Err(e) => Err(e.into())
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn persists_pending_and_drafts_without_crossing_accounts_or_peer_keys() -> Result<()> {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../.tmp").join(format!("cloud-drafts-{}", uuid::Uuid::new_v4()));
        let dir = root.to_str().unwrap();
        let peer = RelayConnection { origin:"https://cloud.invalid".into(), account_id:"account".into(), gateway_id:"desktop".into(), binding_id:"binding".into(), desktop_public_key:"key".into() };
        let key = "composer-v1:pending-v2:[\"gateway\",\"codex\",\"account\",\"local\",\"thread\"]";
        let change = json!({"key":key,"value":"{\"id\":\"unknown-write\"}"}).to_string();
        let store = Store::open(dir,"profile",&peer)?;
        store.apply(change.as_bytes())?;
        for state_key in ["bridge-session-activity", "bridge-desktop-session-activity"] {
            store.apply(json!({"key":state_key,"value":"{\"local|thread\":{\"pending\":\"turn:completed\"}}"}).to_string().as_bytes())?;
            assert!(Store::open(dir,"profile",&peer)?.snapshot()?[state_key].as_str().unwrap().contains("turn:completed"));
            store.apply(json!({"key":state_key,"value":null}).to_string().as_bytes())?;
        }
        assert!(store.apply(br#"{"key":"../../secret","value":"bad"}"#).is_err());
        let mut rebound = peer.clone(); rebound.binding_id = "re-enrolled".into();
        assert_eq!(Store::open(dir,"profile",&rebound)?.snapshot()?[key], "{\"id\":\"unknown-write\"}");
        assert_eq!(Store::open(dir,"other-account",&peer)?.snapshot()?,json!({}));
        rebound.desktop_public_key = "replacement".into();
        assert_eq!(Store::open(dir,"profile",&rebound)?.snapshot()?,json!({}));
        store.apply(json!({"key":key,"value":null}).to_string().as_bytes())?;
        assert_eq!(Store::open(dir,"profile",&peer)?.snapshot()?,json!({}));
        Store::remove_profile(dir,"profile")?;
        fs::remove_dir_all(root)?;
        Ok(())
    }
}
