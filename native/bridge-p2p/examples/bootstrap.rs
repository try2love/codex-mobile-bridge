//! Loopback-only bootstrap for the cross-language integration fixture.
use easytier::{common::config::{ConfigLoader, NetworkIdentity, TomlConfigLoader}, instance::instance::Instance};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let reservation = std::net::TcpListener::bind("127.0.0.1:0")?;
    let port = reservation.local_addr()?.port();
    let config = TomlConfigLoader::default();
    config.set_network_identity(NetworkIdentity::new("bridge-test-bootstrap".into(), "isolated".into()));
    config.set_listeners(vec![format!("tcp://127.0.0.1:{port}").parse()?]);
    let mut flags = config.get_flags();
    flags.no_tun = true; flags.bind_device = false; flags.disable_upnp = true;
    flags.relay_network_whitelist.clear(); flags.relay_all_peer_rpc = true; flags.disable_relay_data = true;
    config.set_flags(flags);
    drop(reservation);
    let mut instance = Instance::new(config);
    instance.run().await?;
    println!("tcp://127.0.0.1:{port}");
    use tokio::io::AsyncReadExt;
    let mut input = Vec::new(); tokio::io::stdin().read_to_end(&mut input).await?;
    Ok(())
}
