// Bridge adaptation of the MIT/Apache-2.0 pnet_datalink Windows backend.
// Application-only EasyTier needs interface enumeration, never packet capture.
// Use Windows IP Helper (network-interface) and explicitly reject raw channels.
use crate::{Channel, NetworkInterface};
use network_interface::{NetworkInterface as SystemInterface, NetworkInterfaceConfig};
use std::{io, net::IpAddr};

pub type Config = crate::Config;
impl From<&crate::Config> for Config {
    fn from(value: &crate::Config) -> Self { *value }
}

pub fn channel(_: &NetworkInterface, _: Config) -> io::Result<Channel> {
    Err(io::Error::new(io::ErrorKind::Unsupported, "Bridge does not support raw packet capture"))
}

pub fn interfaces() -> Vec<NetworkInterface> {
    SystemInterface::show().unwrap_or_default().into_iter().map(|interface| {
        let ips: Vec<_> = interface.addr.iter().filter_map(|addr| {
            let ip = addr.ip();
            let prefix = match addr.netmask() {
                Some(IpAddr::V4(mask)) => u32::from(mask).count_ones() as u8,
                Some(IpAddr::V6(mask)) => u128::from(mask).count_ones() as u8,
                None => if ip.is_ipv4() { 32 } else { 128 },
            };
            ipnetwork::IpNetwork::new(ip, prefix).ok()
        }).collect();
        let loopback = ips.iter().any(|address| address.ip().is_loopback());
        NetworkInterface {
            description: interface.name.clone(), name: interface.name, index: interface.index,
            mac: interface.mac_addr.and_then(|value| value.parse().ok()), ips,
            flags: pnet_sys::IFF_UP as u32 | if loopback { pnet_sys::IFF_LOOPBACK as u32 } else { 0 },
        }
    }).collect()
}
