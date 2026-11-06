use anyhow::{Result, bail, ensure};
#[cfg(test)]
use base64::{Engine, engine::general_purpose::STANDARD};
use snow::{Builder, HandshakeState, TransportState};
use uuid::Uuid;

pub const MAX_CHUNK: usize = 32_768;
const CONTROL: &str = "Noise_KK_25519_ChaChaPoly_BLAKE2s";
pub use crate::origin::{exact_origin, key};

fn origin_bytes(origin: &str) -> Result<Vec<u8>> {
    exact_origin(origin)?;
    let mut bytes = (origin.len() as u16).to_be_bytes().to_vec();
    bytes.extend_from_slice(origin.as_bytes());
    Ok(bytes)
}

pub fn new_keypair() -> Result<snow::Keypair> {
    Ok(Builder::new(CONTROL.parse()?).generate_keypair()?)
}

pub fn public_key(private: &[u8]) -> Result<[u8; 32]> {
    use snow::resolvers::{CryptoResolver, DefaultResolver};
    ensure!(private.len() == 32, "invalid private key size");
    let mut dh = DefaultResolver
        .resolve_dh(&snow::params::DHChoice::Curve25519)
        .ok_or_else(|| anyhow::anyhow!("X25519 unavailable"))?;
    dh.set(private);
    Ok(dh.pubkey().try_into()?)
}

pub fn control_prologue(
    origin: &str,
    gateway: Uuid,
    binding: Uuid,
    channel: Uuid,
    desktop: &[u8; 32],
    mobile: &[u8; 32],
) -> Result<Vec<u8>> {
    let mut out = b"bridge/control/v2\0".to_vec();
    out.extend(origin_bytes(origin)?);
    for id in [gateway, binding, channel] {
        out.extend_from_slice(id.as_bytes());
    }
    out.extend_from_slice(desktop);
    out.extend_from_slice(mobile);
    Ok(out)
}

#[allow(clippy::too_many_arguments)]
pub fn pairing_safety_code(
    origin: &str,
    gateway: Uuid,
    binding: Uuid,
    private: &[u8],
    remote: &[u8],
    desktop: &[u8],
    mobile: &[u8],
    desktop_preview: &[u8],
    mobile_preview: &[u8],
) -> Result<String> {
    use sha2::{Digest, Sha256};
    use snow::resolvers::{CryptoResolver, DefaultResolver};
    ensure!(
        [
            private,
            remote,
            desktop,
            mobile,
            desktop_preview,
            mobile_preview
        ]
        .iter()
        .all(|v| v.len() == 32),
        "invalid pairing key"
    );
    let mut dh = DefaultResolver
        .resolve_dh(&snow::params::DHChoice::Curve25519)
        .ok_or_else(|| anyhow::anyhow!("X25519 unavailable"))?;
    dh.set(private);
    let mut shared = zeroize::Zeroizing::new([0u8; 32]);
    dh.dh(remote, shared.as_mut())?;
    ensure!(shared.iter().any(|b| *b != 0), "invalid shared key");
    let mut hash = Sha256::new();
    hash.update(b"bridge/pairing-safety/v1\0");
    hash.update(origin_bytes(origin)?);
    hash.update(gateway.as_bytes());
    hash.update(binding.as_bytes());
    hash.update(shared.as_ref());
    for key in [desktop, mobile, desktop_preview, mobile_preview] {
        hash.update(key);
    }
    let code: String = hash.finalize().iter().map(|b| format!("{b:02X}")).collect();
    Ok((0..64)
        .step_by(8)
        .map(|i| &code[i..i + 8])
        .collect::<Vec<_>>()
        .join(" "))
}

#[cfg(test)]
mod pairing_tests {
    use super::*;
    #[test]
    fn safety_code_matches_python_and_binds_both_preview_keys() -> Result<()> {
        let private: Vec<u8> = (1..=32).collect();
        let other: Vec<u8> = (33..=64).collect();
        let desktop = public_key(&private)?;
        let mobile = public_key(&other)?;
        let dp = public_key(&[b'D'; 32])?;
        let mp = public_key(&[b'M'; 32])?;
        let gateway = Uuid::parse_str("11111111-1111-4111-8111-111111111111")?;
        let binding = Uuid::parse_str("22222222-2222-4222-8222-222222222222")?;
        let code = pairing_safety_code(
            "https://relay.example.test",
            gateway,
            binding,
            &private,
            &mobile,
            &desktop,
            &mobile,
            &dp,
            &mp,
        )?;
        assert_eq!(
            code,
            "49B47ECB D96CF844 161D3BED 6460DFBF 159F38A3 09DE41DF 6EBB82D3 A96A5E78"
        );
        assert_eq!(
            code,
            pairing_safety_code(
                "https://relay.example.test",
                gateway,
                binding,
                &other,
                &desktop,
                &desktop,
                &mobile,
                &dp,
                &mp
            )?
        );
        assert_ne!(
            code,
            pairing_safety_code(
                "https://relay.example.test",
                gateway,
                binding,
                &private,
                &mobile,
                &desktop,
                &mobile,
                &mp,
                &dp
            )?
        );
        assert!(
            pairing_safety_code(
                "https://relay.example.test",
                gateway,
                binding,
                &private,
                &[0; 32],
                &desktop,
                &mobile,
                &dp,
                &mp
            )
            .is_err()
        );
        Ok(())
    }
}

pub fn handshake(
    private: &[u8],
    remote: &[u8],
    prologue: &[u8],
    initiator: bool,
) -> Result<HandshakeState> {
    let builder = Builder::new(CONTROL.parse()?)
        .local_private_key(private)?
        .remote_public_key(remote)?
        .prologue(prologue)?;
    Ok(if initiator {
        builder.build_initiator()?
    } else {
        builder.build_responder()?
    })
}

pub fn handshake_read(state: &mut HandshakeState, message: &[u8]) -> Result<()> {
    ensure!(message.len() <= MAX_CHUNK + 16, "handshake too large");
    let mut out = vec![0; MAX_CHUNK];
    ensure!(
        state.read_message(message, &mut out)? == 0,
        "early application data forbidden"
    );
    Ok(())
}

pub fn handshake_write(state: &mut HandshakeState) -> Result<Vec<u8>> {
    let mut out = vec![0; MAX_CHUNK + 16];
    let size = state.write_message(&[], &mut out)?;
    out.truncate(size);
    Ok(out)
}

pub struct ControlCipher {
    state: TransportState,
    failed: bool,
}
impl ControlCipher {
    pub fn new(state: HandshakeState) -> Result<Self> {
        ensure!(state.is_handshake_finished(), "handshake incomplete");
        Ok(Self {
            state: state.into_transport_mode()?,
            failed: false,
        })
    }
    pub fn encrypt(&mut self, plaintext: &[u8]) -> Result<Vec<u8>> {
        if self.failed || plaintext.len() > MAX_CHUNK {
            self.failed = true;
            bail!("channel closed or chunk too large");
        }
        let mut out = vec![0; plaintext.len() + 16];
        match self.state.write_message(plaintext, &mut out) {
            Ok(size) => {
                out.truncate(size);
                Ok(out)
            }
            Err(_) => {
                self.failed = true;
                bail!("record authentication failed")
            }
        }
    }
    pub fn decrypt(&mut self, ciphertext: &[u8]) -> Result<Vec<u8>> {
        if self.failed || !(16..=MAX_CHUNK + 16).contains(&ciphertext.len()) {
            self.failed = true;
            bail!("channel closed or invalid record size");
        }
        let mut out = vec![0; ciphertext.len()];
        match self.state.read_message(ciphertext, &mut out) {
            Ok(size) => {
                out.truncate(size);
                Ok(out)
            }
            Err(_) => {
                self.failed = true;
                bail!("record authentication failed")
            }
        }
    }
}

#[derive(Debug, Clone)]
pub struct Frame {
    pub kind: u8,
    pub binding: Uuid,
    pub channel: Uuid,
    pub payload: Vec<u8>,
}
impl Frame {
    pub fn encode(&self) -> Result<Vec<u8>> {
        ensure!(
            match self.kind {
                1 => self.payload.len() == 32,
                2 => (16..=MAX_CHUNK + 16).contains(&self.payload.len()),
                3 => self.payload.is_empty(),
                _ => false,
            },
            "invalid relay frame"
        );
        let mut out = vec![2, self.kind];
        out.extend_from_slice(self.binding.as_bytes());
        out.extend_from_slice(self.channel.as_bytes());
        out.extend_from_slice(&self.payload);
        Ok(out)
    }
    pub fn decode(raw: &[u8]) -> Result<Self> {
        ensure!(raw.len() >= 34 && raw[0] == 2, "invalid relay version");
        let frame = Self {
            kind: raw[1],
            binding: Uuid::from_slice(&raw[2..18])?,
            channel: Uuid::from_slice(&raw[18..34])?,
            payload: raw[34..].to_vec(),
        };
        frame.encode()?;
        Ok(frame)
    }
}
