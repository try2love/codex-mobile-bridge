//! Application-only EasyTier transport. No TUN, exit node, LAN proxy or data relay.
use std::net::{Ipv4Addr, SocketAddr};

use anyhow::{Result, ensure};
use easytier::{
    common::config::{ConfigLoader, NetworkIdentity, PeerConfig, PortForwardConfig, TomlConfigLoader},
    instance::instance::Instance,
    proto::acl::{Acl, AclV1, Action, Chain, ChainType, Protocol, Rule},
};
use serde::{Deserialize, Serialize};
mod bootstrap;

pub const UPSTREAM: &str = "8428a89d2dabc94c97d370ec607c6ca142473626";
const DESKTOP: &str = "10.253.0.1";
const MOBILE: &str = "10.253.0.2";

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Settings {
    pub network: String,
    pub secret: String,
    pub bootstrap: Vec<String>,
    pub desktop: bool,
    /// The desktop's dedicated loopback Noise endpoint, never its HTTP service.
    pub service_port: u16,
}

impl Settings {
    fn validate(&self) -> Result<()> {
        ensure!(self.network.len() >= 32 && self.network.len() <= 80, "invalid network");
        ensure!(self.network.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-'), "invalid network");
        ensure!(self.secret.len() == 64 && self.secret.bytes().all(|b| b.is_ascii_hexdigit()), "invalid secret");
        ensure!(self.service_port != 0, "invalid service port");
        ensure!(!self.bootstrap.is_empty() && self.bootstrap.len() <= 4, "invalid bootstrap count");
        for endpoint in &self.bootstrap {
            let url = url::Url::parse(endpoint)?;
            ensure!(matches!(url.scheme(), "tcp" | "udp" | "wss") && url.host_str().is_some()
                && (url.port().is_some() || url.scheme() == "wss") && url.username().is_empty() && url.password().is_none()
                && url.query().is_none() && url.fragment().is_none()
                && (if url.scheme() == "wss" { url.path() == "/v1/p2p/bootstrap" } else { matches!(url.path(), "" | "/") }), "invalid bootstrap endpoint");
        }
        Ok(())
    }

    fn config(&self, forward_port: u16) -> Result<TomlConfigLoader> {
        self.validate()?;
        let config = TomlConfigLoader::default();
        config.set_inst_name(format!("bridge-{}", self.network));
        config.set_network_identity(NetworkIdentity::new(self.network.clone(), self.secret.clone()));
        config.set_ipv4(Some(format!("{}/24", if self.desktop { DESKTOP } else { MOBILE }).parse()?));
        config.set_dhcp(false);
        config.set_listeners(vec!["udp://0.0.0.0:0".parse()?]);
        config.set_peers(self.bootstrap.iter().map(|uri| Ok(PeerConfig {
            uri: uri.parse()?, peer_public_key: None,
        })).collect::<Result<Vec<_>>>()?);
        let mut flags = config.get_flags();
        flags.no_tun = true;
        flags.use_smoltcp = true;
        flags.bind_device = false;
        flags.p2p_only = true;
        flags.need_p2p = true;
        flags.disable_upnp = true;
        flags.relay_network_whitelist.clear();
        flags.disable_relay_data = true;
        flags.encryption_algorithm = "aes-gcm".into();
        config.set_flags(flags);
        if self.desktop {
            // Only this mapped /32 exists, and ACL below narrows it to one TCP port.
            config.add_proxy_cidr("127.0.0.1/32".parse()?, Some(format!("{DESKTOP}/32").parse()?))?;
        } else {
            config.set_port_forwards(vec![PortForwardConfig {
                bind_addr: SocketAddr::from((Ipv4Addr::LOCALHOST, forward_port)),
                dst_addr: format!("{DESKTOP}:{}", self.service_port).parse()?,
                proto: "tcp".into(),
            }]);
        }
        // Explicit request and response rules also protect stateless fragments/flows.
        let request = Rule {
            name: "bridge-request".into(), enabled: true, priority: 100,
            protocol: Protocol::Tcp as i32, action: Action::Allow as i32,
            source_ips: vec![format!("{MOBILE}/32")], destination_ips: vec![format!("{DESKTOP}/32")],
            ports: vec![self.service_port.to_string()], stateful: true, ..Default::default()
        };
        let response = Rule {
            name: "bridge-response".into(), enabled: true, priority: 100,
            protocol: Protocol::Tcp as i32, action: Action::Allow as i32,
            source_ips: vec![format!("{DESKTOP}/32")], destination_ips: vec![format!("{MOBILE}/32")],
            source_ports: vec![self.service_port.to_string()], stateful: true, ..Default::default()
        };
        config.set_acl(Some(Acl { acl_v1: Some(AclV1 {
            chains: [ChainType::Inbound, ChainType::Outbound, ChainType::Forward].into_iter().map(|kind| {
                let mut rules = vec![request.clone(), response.clone()];
                // EasyTier checks outbound ACL before rewriting its smoltcp proxy
                // source back to the public service port. This internal tuple is
                // never accepted inbound and cannot reach another host port.
                if self.desktop && kind == ChainType::Outbound {
                    rules.push(Rule { name: "bridge-smoltcp-response".into(), enabled: true, priority: 101,
                        protocol: Protocol::Tcp as i32, action: Action::Allow as i32,
                        source_ips: vec!["192.88.99.254/32".into()], destination_ips: vec![format!("{MOBILE}/32")],
                        source_ports: vec!["8899".into()], ..Default::default() });
                }
                Chain {
                name: format!("bridge-{kind:?}"), chain_type: kind as i32, enabled: true,
                default_action: Action::Drop as i32, rules,
                ..Default::default()
            }}).collect(), ..Default::default()
        }) }));
        Ok(config)
    }
}

/// Ownership stops all instance tasks. Mobile keeps this alive with the Noise session.
pub struct Runtime {
    _instance: Instance,
    pub local_port: u16,
    _bootstrap_tasks: BootstrapTasks,
}

#[derive(Default)]
struct BootstrapTasks(Vec<tokio::task::JoinHandle<()>>);
impl Drop for BootstrapTasks {
    fn drop(&mut self) { for task in &self.0 { task.abort(); } }
}

impl Runtime {
    pub async fn start(mut settings: Settings) -> Result<Self> {
        settings.validate()?;
        let mut bootstrap_tasks = BootstrapTasks::default();
        for endpoint in &mut settings.bootstrap {
            if endpoint.starts_with("wss://") {
                let (local, task) = bootstrap::start(endpoint.clone()).await?;
                *endpoint = local; bootstrap_tasks.0.push(task);
            }
        }
        // Reserve a loopback port; requests through it are independently Noise authenticated.
        let reservation = std::net::TcpListener::bind((Ipv4Addr::LOCALHOST, 0))?;
        let local_port = reservation.local_addr()?.port();
        let config = settings.config(local_port)?;
        let mut instance = Instance::new(config);
        drop(reservation);
        instance.run().await?;
        Ok(Self { _instance: instance, local_port, _bootstrap_tasks: bootstrap_tasks })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn settings() -> Settings {
        Settings { network: "a".repeat(32), secret: "b".repeat(64), bootstrap: vec!["tcp://127.0.0.1:11010".into()], desktop: true, service_port: 50000 }
    }
    #[test]
    fn rejects_endpoint_credentials_and_arbitrary_proxy_config() {
        let mut value = settings(); value.bootstrap = vec!["tcp://user:secret@host:11010".into()];
        assert!(value.validate().is_err());
        let mut raw = serde_json::to_value(settings()).unwrap(); raw["proxy_cidr"] = serde_json::json!("0.0.0.0/0");
        assert!(serde_json::from_value::<Settings>(raw).is_err());
    }
    #[test]
    fn configuration_is_application_only() {
        let config = settings().config(50001).unwrap();
        let flags = config.get_flags();
        assert!(flags.no_tun && flags.p2p_only && flags.disable_relay_data);
        assert!(!flags.enable_exit_node && flags.relay_network_whitelist.is_empty());
        assert!(config.get_socks5_portal().is_none());
        assert_eq!(config.get_proxy_cidrs().len(), 1);
        for chain in config.get_acl().unwrap().acl_v1.unwrap().chains {
            assert_eq!(chain.default_action, Action::Drop as i32);
        }
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn real_overlay_survives_bootstrap_loss_and_blocks_other_ports() {
        let _ = tracing_subscriber::fmt().with_env_filter(tracing_subscriber::EnvFilter::from_default_env()).with_test_writer().try_init();
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        use std::time::Duration;
        fn unused_port() -> u16 {
            std::net::TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap().local_addr().unwrap().port()
        }
        let seed_port = unused_port();
        let seed_config = TomlConfigLoader::default();
        seed_config.set_network_identity(NetworkIdentity::new("bridge-test-seed".into(), "seed".into()));
        seed_config.set_listeners(vec![format!("tcp://127.0.0.1:{seed_port}").parse().unwrap()]);
        let mut flags = seed_config.get_flags();
        flags.no_tun = true; flags.bind_device = false; flags.relay_network_whitelist.clear();
        flags.relay_all_peer_rpc = true; flags.disable_relay_data = true; flags.disable_upnp = true;
        seed_config.set_flags(flags);
        let mut seed = Instance::new(seed_config);
        seed.run().await.unwrap();
        let service = tokio::net::TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
        let service_port = service.local_addr().unwrap().port();
        let forbidden = tokio::net::TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
        let forbidden_port = forbidden.local_addr().unwrap().port();
        let mut desktop = settings();
        desktop.service_port = service_port;
        desktop.bootstrap = vec![format!("tcp://127.0.0.1:{seed_port}")];
        let _desktop = Runtime::start(desktop.clone()).await.unwrap();
        desktop.desktop = false;
        let mobile_port = unused_port();
        let blocked_port = unused_port();
        let mobile_config = desktop.config(mobile_port).unwrap();
        let mut forwards = mobile_config.get_port_forwards();
        forwards.push(PortForwardConfig { bind_addr: (Ipv4Addr::LOCALHOST, blocked_port).into(),
            dst_addr: format!("{DESKTOP}:{forbidden_port}").parse().unwrap(), proto: "tcp".into() });
        mobile_config.set_port_forwards(forwards);
        let mut mobile = Instance::new(mobile_config);
        mobile.run().await.unwrap();
        let echo = tokio::spawn(async move {
            let (mut socket, _) = service.accept().await.unwrap();
            let mut buffer = [0; 4096];
            loop {
                let count = socket.read(&mut buffer).await.unwrap();
                if count == 0 { break; }
                socket.write_all(&buffer[..count]).await.unwrap();
            }
        });
        // Wait for direct routes before opening the application flow.
        tokio::time::timeout(Duration::from_secs(40), async {
            while !mobile.get_peer_manager().get_peer_map().list_peers_with_conn().await.contains(&_desktop._instance.peer_id()) {
                tokio::time::sleep(Duration::from_millis(100)).await;
            }
        }).await.unwrap();
        let mut socket = tokio::net::TcpStream::connect((Ipv4Addr::LOCALHOST, mobile_port)).await.unwrap();
        socket.write_all(b"before").await.unwrap();
        let mut reply = [0; 6];
        tokio::time::timeout(Duration::from_secs(40), socket.read_exact(&mut reply)).await.unwrap().unwrap();
        assert_eq!(&reply, b"before");
        drop(seed);
        socket.write_all(b"after!").await.unwrap();
        tokio::time::timeout(Duration::from_secs(10), socket.read_exact(&mut reply)).await.unwrap().unwrap();
        assert_eq!(&reply, b"after!");
        // A malicious peer can ignore its own ACL. The desktop must still refuse
        // other local services. Disable only the test sender's rules now.
        mobile.get_peer_manager().get_global_ctx().get_acl_filter().reload_rules(None);
        let mut attempt = tokio::net::TcpStream::connect((Ipv4Addr::LOCALHOST, blocked_port)).await.unwrap();
        attempt.write_all(b"must not reach host").await.unwrap();
        assert!(tokio::time::timeout(Duration::from_secs(3), forbidden.accept()).await.is_err());
        drop(socket); echo.abort();
    }
}
