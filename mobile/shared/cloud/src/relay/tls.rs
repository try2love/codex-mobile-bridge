use std::sync::Arc;
use std::time::Duration;

use rustls_platform_verifier::ConfigVerifierExt;
use tokio_tungstenite::{
    Connector,
    tungstenite::{
        Error,
        handshake::client::{Request, Response},
        protocol::WebSocketConfig,
    },
};

use super::Socket;
use crate::error::{BridgeError, Result};

fn platform_connector() -> Result<Connector> {
    // Native-root file probing misses Android/iOS trust; use the same OS verifier as HTTPS.
    let config = rustls::ClientConfig::with_platform_verifier().map_err(|_| BridgeError::Tls)?;
    Ok(Connector::Rustls(Arc::new(config)))
}

pub(super) async fn connect(
    request: Request,
    config: WebSocketConfig,
) -> Result<(Socket, Response)> {
    connect_with_connector(request, config, platform_connector()?).await
}

async fn connect_with_connector(
    request: Request,
    config: WebSocketConfig,
    connector: Connector,
) -> Result<(Socket, Response)> {
    tokio::time::timeout(
        Duration::from_secs(15),
        tokio_tungstenite::connect_async_tls_with_config(
            request,
            Some(config),
            false,
            Some(connector),
        ),
    )
    .await
    .map_err(|_| BridgeError::Network)?
    .map_err(connection_error)
}

fn connection_error(error: Error) -> BridgeError {
    match error {
        Error::Tls(_) => BridgeError::Tls,
        Error::Io(error)
            if error
                .get_ref()
                .and_then(|source| source.downcast_ref::<rustls::Error>())
                .is_some_and(|source| matches!(source, rustls::Error::InvalidCertificate(_))) =>
        {
            BridgeError::Tls
        }
        Error::Http(response) if response.status().is_redirection() => BridgeError::Redirect,
        Error::Http(response) => BridgeError::Relay(
            match response.status().as_u16() {
                401 => "login_required",
                402 => "entitlement_required",
                403 => "access_denied",
                409 => "already_connected",
                426 | 400 => "protocol_upgrade_required",
                429 => "capacity",
                503 => "gateway_offline",
                _ => "unavailable",
            }
            .into(),
        ),
        _ => BridgeError::Network,
    }
}
