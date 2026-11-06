use serde::{Deserialize,Serialize};

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub struct Computer {
    pub id: String,
    pub name: String,
    pub base_url: String,

    pub connection_type: ConnectionType,
    pub relay: Option<RelayConnection>,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum ConnectionType {
    #[default]
    Direct,
    Relay,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct RelayConnection {
    pub origin: String,
    pub account_id: String,
    pub gateway_id: String,
    pub binding_id: String,
    pub desktop_public_key: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CloudCapabilities {
    pub mode: String,
    pub login: String,
    pub registration: bool,
    pub entitlement_required: bool,
    pub umeng: bool,
    pub apns: bool,
    pub umeng_app_id: String,
    pub apns_app_id: String,
    #[serde(default)]

    pub automatic_discovery: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CloudLogin {
    pub account_id: String,
    pub login: String,
    pub credentials: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct RelayEnrollment {
    pub computer: Computer,
    pub account_credentials: String,
    pub binding_credentials: String,
    pub mobile_control_public_key: String,
    pub mobile_preview_public_key: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CloudGateway {
    pub origin: String,
    pub id: String,
    pub name: String,
    pub control_public_key: String,
    pub online: bool,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum CloudDeviceStatus {
    Pending,
    Active,
    Disabled,
    Revoked,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CloudDiscoveredDevice {
    pub gateway: CloudGateway,
    pub binding_id: String,
    pub status: CloudDeviceStatus,
    pub access_revision: i64,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct RelayPairingState {
    pub status: String,
    pub safety_code: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CloudEntitlement {
    pub active: bool,
    pub required: bool,
    pub expires_at: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CloudCredentialState {
    pub origin: String,
    pub account_id: String,
    pub login: String,
    pub consumed_credentials: Option<String>,
}
