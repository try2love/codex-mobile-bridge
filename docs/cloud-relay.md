# Cloud relay and encrypted notifications

This describes the source protocol; dated deployment and validation results are
recorded separately, not a guarantee of signed push delivery. Direct LAN/HTTPS, SSH, and existing
shared-relay access remain independent. No real coding-client session is created,
resumed, or switched by cloud notification observation.

## Desktop setup and approval

Source desktop runs require the Python dependencies in `requirements-desktop.txt`,
including `aiohttp`, `noiseprotocol` (imported as `noise`) and `keyring`. Install
them in the interpreter used by `CMB_PYTHON` or the desktop's `python` executable,
then restart the gateway. Desktop builds include these dependencies and fail
early if they are missing in the build environment. A `cloud_dependencies_missing`
status means the local connector could not import its runtime dependencies, not
that the official cloud endpoint failed.

After cloud IPC or Python-controller source updates, restart both the desktop
application and gateway. Reloading only the renderer does not replace cached
main-process IPC validation or the running Python controller.

The Electron network panel contains **Cloud Relay**. Its operations use the
existing local management worker and a field-validated IPC allowlist; they are
not exposed as new unauthenticated gateway HTTP administration endpoints.

When **Allow web access to this computer** is enabled, the desktop reads the
selected server's `capabilities.web_client.origin` and shows **Web cloud
address** below the switch. Click the read-only address to select it for copying,
then sign in in a browser using the same cloud account. Only an exact HTTPS
origin is displayed. Turning the switch off hides the address; failures or an
unavailable server entry offer **Refresh address**. The address is an entry
point, not a claim that the gateway is connected or the account is entitled.

1. Click **Sign In to Cloud** to open the login dialog. **Official Cloud** is
   the first server option and the default for a new account, using
   `https://bridge.waken.cloudns.be:8787`. Select **Custom Server** to enter a
   self-hosted exact canonical HTTPS origin, without a path, query, fragment,
   credentials, or explicit default `:443` port. Existing custom origins remain
   selected for an already configured account. Opening the dialog or changing
   servers does not sign in, connect or move any account/device authorization;
   changing the selected origin clears the unsent password and verification code.
   **Forgot password?** sits beside the password field and **Create an account**
   is visible below the sign-in button. Only the recovery button opens password
   reset; surrounding whitespace does not, and the password label focuses its input.
   Email-code entry and **Resend code** appear
   only inside the account-creation flow, not on the sign-in or recovery screen.
   Each opens a dedicated form in the same dialog rather than a hidden drawer.
   There is no server-detection button; capability discovery remains internal to
   explicit sign-in so hosted/self-host entitlement rules still come from the
   selected server.
   The official endpoint is used internally; both the login dialog and signed-in
   account summary show only **Official Cloud**, not its hostname or port.
   Hosted mode requires verified email and account-wide service days; independent
   selfhost mode uses administrator-created accounts and no official payment service.
2. Log in to that selected origin/account. Hosted registration, verification,
   password-reset requests and service-days redemption have explicit desktop
   controls. Service qualification is queried on login, confirmed redemption and
   explicit refresh; no client timer polls it. The desktop's **Sponsor support**
   section offers **Buy a 30-day Official Cloud activation code (Xianyu)** when
   Official Cloud is selected before login or the signed-in account uses the
   official server, including inactive or expired service. Clicking the purchase
   button opens a QR dialog and reads the image from the official server's
   `GET /v1/sponsorship` endpoint. The desktop does not store a purchase URL or
   generate a purchase QR code. Each reopening fetches the current image;
   language changes only repaint the dialog. After purchase, sign in and redeem
   the code on this page. The image request sends no account credentials.
   Custom-server accounts do not show this section.
   Purchase entry and display rules are recorded in the
   [purchase dialog decision](../.agents/notes/implemented/feature/2026-11-11-sponsor-dialog-and-web-address.md).
   The mobile app does not add a purchase or external-purchase link.
   The dialog closes after the login result and current
   account are confirmed; errors stay visible without replaying the operation.
   Closing or dismissing the dialog clears passwords and the verification code.
   Registration first displays the email and six-digit code together, with **Get
   code** beside the code input. Passwords stay hidden and disabled until the
   server verifies the code. The second step locks the verified email and asks
   for a new password and confirmation; only completing this step creates the
   account, then returns to sign-in. Verification alone creates no account and
   does not enable the relay or approve a phone. Codes expire after ten minutes;
   resending has a sixty-second countdown and invalidates the previous code and
   any password-stage proof. The single-use proof is bound to the exact server
   and email, expires after ten minutes and stays only in dialog memory. Closing
   the dialog, changing email/server or submitting completion clears it. Recovery and resend
   request only the email; accepted receipts do not reveal account existence or
   confirm actual delivery. Unknown outcomes are never automatically replayed.
   Email verification links are removed from the server. Clients check for code
   support and `registration_flow: "email_code_password"` before signup; old servers must be
   updated rather than silently reverting to password-first signup or links. Password-reset links are
   retained as a separate flow.
3. The sign-in button disappears after confirmed login and returns after local
   logout. There is no manual relay-enable button. A verified active hosted
   entitlement is enforced by the server when opening and maintaining the relay; eligible
   selfhost accounts do not need paid service days. Start the ordinary gateway
   runtime separately if it is stopped. Account management does not start a
   coding client, gateway or session. The first encrypted identity check needs
   both devices online; previously verified devices remain listed while offline.
   The gateway's notification inbox and chat titles use the upstream defaults:
   enabled when no choice has been saved, with saved choices taking precedence.
   Cloud login and service checks do not inspect or change notification settings.
   Native notification permissions, per-chat reminders and **Open notification
   links in the mobile app** remain separate settings. The
   [upstream integration decision](../.agents/notes/implemented/process/2026-11-08-upstream-v2.2.0-integration.md)
   records removal of the former cloud-specific inbox initialization.
4. Sign in on the phone. Device enrollment creates stable pending relationships
   with every non-revoked same-account desktop, regardless of login order. There
   is no **Request pairing** button, target selection, QR, camera request or expiry.
   Discovery is a cloud directory sync, not LAN broadcasting or a background phone service.
   The phone saves its keys before sending encrypted identity proof automatically.
5. The desktop displays a unified **Cloud Devices** list with a default-off
   **Allow Connection** switch. Discovery never opens an approval dialog automatically.
   Compare the eight-group safety fingerprint on the phone and desktop before enabling it.
   The code binds the exact origin, computer/request IDs, both control keys and
   both preview keys to an X25519 shared secret. Server membership alone is not
   approval; the phone must demonstrate its key through the encrypted channel.
6. Connections remain pending until the desktop explicitly allows them.
   Closing a view, disconnecting or restarting does not expire the relationship.
   Network attempts have bounded transport waits, not a pairing deadline. An
   unavailable account/service cannot authorize access but does not silently grant
   or expire discovery. Closing **Allow Connection** blocks just that phone locally,
   cancels its channels and queued notifications, and then updates the server.
   `disabled` retains verified keys; restoring the switch needs no phone request.
   Permanent `revoked` records are not revived. A lost opening response leaves
   local access blocked, and stale openings cannot overwrite a later close.
   Discovery/approval does not start or switch a coding-client session.

This prerelease protocol requires coordinated desktop/mobile/server sources and
fresh development storage. Server capabilities advertise
`device_discovery: account_directory_v1` and `binding_access: desktop_switch_v1`.
The old manual request/approve/reject/revoke IPC and HTTP paths, pairing tables
and migration adapters are removed. PostgreSQL initializes from `001_current.sql`;
old migration histories fail explicitly. App SQLite creates the current schema
directly, and saved cloud credentials must use the complete current format.
There is no automatic data deletion or upgrade from an older development database.
Current devices retain their verified keys through ordinary close/restore, and
session cleanup cannot delete their identities simply because a session is old.
The [prerelease contract decision](../.agents/notes/implemented/simplification/2026-11-04-prerelease-cloud-contract-cleanup.md)
records the removal boundary and verification.

The cloud API verifies TLS and does not follow redirects. Similar hosts, another
account or a changed origin cannot reuse stored credentials or local approval.
If a write/refresh loses its response, refresh/reconcile status rather than
blindly resubmitting a possibly consumed operation. Cloud access does not replace
the original provider, host, conversation, owner, authentication or working directory.

## Local storage and lifecycle

- The application layer creates a separate ephemeral loopback HTTP listener and
  injects its factory into the cloud connector. Runtime dependencies are checked
  before the listener is allocated. It reuses the original business services and
  concurrency limits, but not direct-login auth, sessions, cookie namespaces or
  pairing authority. Closing it does not close the shared business services.
- Account configuration in the private data directory stores origin, account ID,
  login and gateway ID, not passwords/tokens/private keys. Historical enabled
  values do not grant or prevent server authorization. Signing out clears local activation.
- Account/device tokens and control/preview key material use the native OS vault,
  scoped by exact origin and account ID. Vault failure is an error; no plaintext
  credential fallback is permitted.
- `cloud-entitlement.json` caches scoped display metadata. Neither the connector
  nor the panel schedules qualification queries. Hosted eligibility, real expiry,
  account/session validity and revocation are checked by the cloud server;
  changing client code or clocks cannot bypass them. A failed display query does
  not interrupt an independently authorized live connection.
- Confirmed service-days redemption immediately reads entitlement and returns
  the receipt plus updated service status. If that read fails, the desktop says
  the code was redeemed but the status refresh failed; it must not submit the
  code again automatically. Lost/unconfirmed redemption writes keep the existing
  scoped request journal and are not replayed. Service display reads retry only
  when explicitly requested.
- Local approval pins and runtime status remain private. Account change, expiry,
  logout or binding revocation fences old channels and unapproved device proofs.
  Logout clears local account activation and vault tokens before attempting remote
  session revocation. It never rotates a refresh token or requires signing in again.
  The result separates confirmed local logout, remote revocation confirmation and
  credential-vault cleanup errors. Missing/expired credentials or an offline server
  do not keep the desktop signed in; unavailable vaults get an explicit cleanup
  warning, not a claim that stored credentials were erased. Local key material and
  scoped approval pins remain in the native vault/private data directory.
  The renderer restores sign-in from the confirmed logout result without requiring
  another status read. Unknown logout responses are reconciled with a
  read-only status request, never an automatic logout retry. Late service reads
  cannot restore old entitlements, and a signed-out snapshot received before the
  logout response does not discard the completion feedback.
  Offline logout cannot prove the remote session was removed. Offline revocation
  similarly cannot confirm remote removal.
- `cloud-access.json` keeps permission separate from immutable trusted keys.
  A trusted key without an explicit access record never allows a business request.
  Encrypted proofs use `cloud-device-proofs.json`; old proof files are not imported.
  Closing a switch fences disk/runtime access before cloud I/O, including a failed
  credential-vault lookup. If storage itself fails, the running connector keeps an
  in-memory fence and explicitly warns that the disabled state was not saved.
  Reads can reconcile a confirmed close, but cannot silently accept an unknown opening.
  The desktop access POST carries an expected revision; every close advances the
  server fence even when the earlier opening committed first.
- Mobile directory sync runs at login/restore/resume and every three foreground
  seconds, with bounded backoff and no overlapping task. Account changes and
  backgrounding cancel the native HTTP/proof reads; old responses cannot install
  another account's device or keys. The cloud service view and chat busy lock do
  not own this sync. Cloud device info is read-only; only direct connections can
  be removed from the phone.
- Control transport uses `bridge-relay-v2`, with one persistent Noise session per
  binding and eight concurrent logical request streams. Four-chunk credits bound
  each direction; response completion/cancellation affects only its stream.
  Idle heartbeat is 20 seconds with a 10-second response deadline. Reconnection
  uses bounded jittered backoff and never replays uncertain writes. Preview and
  pairing identity formats remain unchanged. Upgrade the three components together;
  this transport upgrade retains current accounts, databases and paired keys.
- Refresh requests persist a `request_id` before networking. The server recovers
  the same encrypted rotation result for five minutes without extending the old
  session lifetime. A different request ID using a spent token still revokes the session.
- `cloud-notifications.sqlite3` is the private durable notification outbox. It
  stores event/binding identity, random notification ID, expiry, encrypted preview
  and bounded retry/cancellation state. It contains no command queue. Initialization
  creates the current tables; historical development schemas are not migrated or
  repaired. Current-format receipts survive normal restarts.
- Cloud notification reads reuse the existing notification policy and original
  passive client state; they must not attach a different owner or activate a chat.

Do not copy the private data directory into a shared folder, build artifact, or
repository. Do not print QR secrets, tokens, preview keys, ciphertext or private
conversation payloads in diagnostics.

## Relay and cloud notifications

The sibling MIT Go/PostgreSQL server serves hosted and independent selfhost modes
through `/v1` account, device-directory, access, WebSocket and push APIs. It is
distinct from this repository's older `relay/` service and direct `/api/mobile/push`
APNs/FCM/Live Activities adapters. U-Push delivery belongs to the cloud server;
the desktop has no direct U-Push registration, ACK or vendor signing adapter.

The private loopback listener uses in-memory sessions scoped to the relay
connection, without a separate browser session deadline. Every forwarded request
checks the current account, entitlement, binding permission and phone key.
Disabling access or disconnecting clears its internal sessions, and an explicitly
revoked session is rejected without issuing a replacement or replaying a request.
Direct browser session lifetimes are unchanged.

After `response_end`, the desktop leaves the per-request channel open for the
phone to close, draining at most four trailing credits for at most five seconds.
It must not race the receiver with an immediate outer Close; missing completion
or cancellation never causes an automatic replay. The
[response-close decision](../.agents/notes/implemented/bug-fix/2026-11-04-cloud-response-peer-close.md)
records the real three-component regression and device verification.

Control records use Noise KK with a 32 KiB maximum. Preview keys are separate and
use single-message Noise K, with at most 1 KiB of plaintext. The complete APNs
JSON is at most 4 KiB; invalid/oversized/unavailable previews allow only safe
generic text or suppression. A preview key cannot execute or approve a command.
Unknown writes are never automatically replayed, and no offline commands are added.

The desktop persists one random notification ID per event and approved binding.
Direct and vendor deliveries share that ID. Mobile persists receipts before ACK;
ACK is not a read marker, approval or command. Direct leases last 45 seconds and
events expire after one hour. Initial observation is quiet. Lost notification
send/cancel responses retry only with the same bounded, idempotent identity and
ciphertext; command writes do not use that retry path.

The cloud Go server, not the desktop/mobile app, holds production vendor signing
secrets. Android client Message Secret and server App Master Secret are different
credentials. Vendors and operators still process installation/routing/delivery
metadata and ciphertext; encrypted previews are not a claim of zero SDK collection.

## Native variants and consent

Ordinary Flutter Android release/profile is vendor-SDK-free. Production U-Push
requires explicit build opt-in and separate native device consent. The Lenovo
debug manual-console grant and credentials do not carry over to production.
Ordinary iOS does not enable APNs or embed the notification service extension;
the `cloud` flavor has a preview-only NSE and separately configured App Group and
Keychain access group. Neither notification handling nor a tap authorizes a chat
action. The companion documents its exact variables and platform gates in
`docs/cloud-notifications.md`, `docs/privacy.md`, and `docs/release-checklist.md`.

Withdrawal blocks new app-owned cloud notifications and resets the quiet baseline,
but keeps unexpired receipts and preview credentials for deduplication/reconciliation.
Native SDK disable, authenticated server endpoint disable, local binding/account
removal and vendor metadata deletion are different operations. Offline/failed
revocation is not confirmed remote removal; U-Push disable is not unregister or
full common/asms deinitialization. Ordinary direct functionality stays available.

## Local validation

Use an isolated Python environment containing `requirements-desktop.txt` (including
`requirements-relay.txt`), never a production data directory. The test runner does
not install missing dependencies; cloud encryption tests need `noiseprotocol`.

```sh
python -B scripts/test-backend.py --source-only -p 'test_cloud_*.py'
python -B scripts/test-backend.py --source-only -p 'test_mobile_push.py'
node --test tests/cloud-desktop.test.cjs tests/cloud-i18n.test.cjs tests/cloud-panel-ui.test.cjs
python -B scripts/test-backend.py --source-only
npm run test:desktop
```

Tests use synthetic accounts/keys and local fixtures. The network listener fixture
explicitly selects an unavailable Codex metadata binary so it cannot accidentally
launch the installed runtime and its Git helpers. Assertions still verify traffic,
listener shutdown, child exit and temporary-directory cleanup; no cleanup failure
is ignored or skipped.

Status/config JSON replacement retries Windows sharing denials for at most two
seconds, preserves the original document on failure, and cleans temporary files.
This does not retry chat commands or suppress persistent filesystem errors.

Go/PostgreSQL tests, Noise vectors, host Flutter/Rust checks, Android compilation,
and native source/CI structure checks are separate from Linux race/Compose execution,
macOS Swift/Xcode, signed-device delivery and store review. See the cloud server's
`docs/implementation-status.md` for dated results and remaining failures. A prior
green CI run or Lenovo console snapshot does not validate the current dirty changes.
