# OTA firmware and version overlay: feasibility and implementation plan

Assessment date: 7 September 2026. Local baseline: `main` at
`d7d8a1ac6f0c26ccecfe5cfb824700e91fd4ef34`. Upstream baseline:
[v1.7.0](https://github.com/chrisjtwomey/inkplate10-weather-cal/releases/tag/v1.7.0),
released 5 September, commit `1fed8e02e93f733e51205ed468daa161934dc837`.
Its server dependency is `epd` v0.2.0, commit
`655ed4f99ce06e7a0341fbcc304dc1625f73bee4`; its firmware declares EpdClient
and EpdBoardInkplate `^0.2.0` through PlatformIO.

**Recommendation: integrate both features through a focused port.** The version
overlay is straightforward and can ship independently. OTA is feasible with the
existing Arduino toolchain and Flask/Gunicorn architecture, but safe deployment
requires a two-slot partition layout, explicit trial-boot handling, configuration
survival, and a one-time USB bootstrap for current firmware. A broad upstream
merge or migration to its `epd` framework would expand the scope substantially.

This is a source-based study, not a tested OTA implementation. I inspected both
tagged upstream repositories, this fork's source and workflows, its installed
board core, existing build artifacts, and decoded its checked-in partition
binary. Existing build sizes below are observations from July builds, not fresh
builds of this baseline. No board was interrogated, flashed, or power-tested.

**What upstream actually implements**

The application draws the downloaded page, adds battery and running-version
overlays, confirms a pending update, then takes any firmware offer before
disconnecting Wi-Fi. Failed downloads leave that successful page in place.
See [upstream app.cpp](https://github.com/chrisjtwomey/inkplate10-weather-cal/blob/1fed8e02e93f733e51205ed468daa161934dc837/src/app.cpp).

Most OTA code is in the separate library:

| Component | Upstream behavior | Recommended treatment here |
| --- | --- | --- |
| Discovery | Client identity headers; firmware version/URL in page response headers | Retain explicit identity, use a separate small OTA manifest request |
| Transfer | Arduino `HTTPUpdate`, a 20-second HTTP timeout, `x-MD5`, restart after successful write | Reuse platform APIs; bind the transfer to an immutable, verified artifact |
| Trial boot | Defer Arduino's early confirmation; confirm after drawing; explicitly roll back on failure | Port the lifecycle, adapting every local early exit |
| Rejection memory | One rejected version in NVS, written by explicit rollback | Add durable attempt tracking that also covers crashes before application code runs |
| Server store | Version-named files; newest modification time wins; previous files deleted on ingest | Use immutable files and an atomically published desired-version manifest |
| Release watcher | Background GitHub poller inside the server lifecycle | Run one independent updater outside Gunicorn workers |
| Version overlay | Draw `CLIENT_VERSION` in the framebuffer before panel refresh | Draw this fork's existing `FIRMWARE_VERSION` |

Sources: [OTA implementation](https://github.com/chrisjtwomey/epd/blob/655ed4f99ce06e7a0341fbcc304dc1625f73bee4/firmware/src/ota.cpp),
[offer policy](https://github.com/chrisjtwomey/epd/blob/655ed4f99ce06e7a0341fbcc304dc1625f73bee4/firmware/src/ota_offer.cpp),
[firmware store and watcher](https://github.com/chrisjtwomey/epd/blob/655ed4f99ce06e7a0341fbcc304dc1625f73bee4/server/epd_server/firmware.py),
[HTTP routes](https://github.com/chrisjtwomey/epd/blob/655ed4f99ce06e7a0341fbcc304dc1625f73bee4/server/epd_server/app.py).

**Where our architecture helps**

[web.py](../server/web.py) already serves persistent artifacts independently of
the weather producer. [web_server.py](../server/web_server.py) launches Gunicorn
with one worker and two threads by default, with configurable concurrency.
[artifacts.py](../server/artifacts.py) already establishes atomic file publication.
Firmware serving fits this model well: weather rendering need not block a
download, and downloads need not invoke weather APIs or a renderer.

The important advantage is the separation of production from serving. Gunicorn
provides concurrency and process supervision, but does not make background
pollers or Python globals shared. Starting upstream's watcher in `create_app()`
would create competing watchers as workers start or restart. Its fixed temporary
filename and deletion of old binaries would compound this problem.

Use a single optional firmware updater process, a persistent artifact directory,
and read-only request handling. Extend container supervision and native systemd
installation for that process. It should retry upstream failures without taking
the web service down, and operate independently of weather refresh cadence.
Gunicorn workers should receive validated OTA serving configuration through the
existing startup/export pattern; currently `web:app` does not load all YAML
settings itself. The updater alone needs any GitHub token.

Do not assume two threads can serve an arbitrary fleet simultaneously. Test a
slow download alongside health, PNG and manifest requests, then size concurrency
and proxy timeouts from measured demand. Serve files without loading the entire
binary into a Python byte buffer as upstream currently does.

**The migration blocker: flash layout**

The checked-in `firmware_binaries/inkplate10-partitions.bin` decodes to NVS,
OTA metadata, a single `ota_0` application at `0x10000` of size `0x300000`,
SPIFFS and coredump. Both existing local build partition CSVs match it. Having
`otadata` does not make a one-application layout OTA-capable.

The pinned Inkplate board package explains this: `Inkplate10V2` lists
`huge_app` (3 MB, no OTA) first in its partition menu, while our Makefile uses
the board name without an explicit partition selection. The generated artifacts
confirm the effective selection. Pin it explicitly instead of relying on defaults.

| Available 4 MB layout | Application slots | Assessment |
| --- | --- | --- |
| Current `huge_app` | One, 3,145,728 bytes | Cannot support the proposed OTA scheme |
| `default` | Two, 1,310,720 bytes each | Existing SD image leaves only 127,024 bytes before OTA additions |
| `min_spiffs` | Two, 1,966,080 bytes each | Preferred candidate; existing SD image leaves 782,384 bytes |

The existing SD and embedded images are respectively 1,183,696 and 1,178,784
bytes. This fork currently does not need upstream's SPIFFS image cache; preserving
only 128 KiB SPIFFS in `min_spiffs` appears reasonable. Confirm actual board flash
capacity and any installation-specific filesystem use before committing to it.
The candidate FQBN is
`Inkplate_Boards:esp32:Inkplate10V2:PartitionScheme=min_spiffs`.

Safe application OTA requires two application slots plus OTA metadata. Plan a
USB bootstrap that installs the selected partition table, a verified compatible
bootloader and OTA-capable firmware, with documented handling of OTA metadata
and existing data. An application-only upload cannot migrate this layout.
See [ESP-IDF 4.4.6 OTA requirements](https://docs.espressif.com/projects/esp-idf/en/v4.4.6/esp32/api-reference/system/ota.html).

Current release assets contain application and partition binaries but no
bootloader. A complete bootstrap bundle therefore needs additional artifacts
and an exact flash recipe. Subsequent OTA offers must contain only the application
image, never the partition table, bootloader or a merged flash image.

**Firmware integration and update confirmation**

The installed Inkplate core 8.1.0 includes HTTPUpdate, Update, Preferences and
ESP OTA APIs. Its SDK headers report ESP-IDF 4.4.6 and enable bootloader rollback.
Its `esp32-hal-misc.c` also implements the weak `verifyRollbackLater()` hook used
upstream. This is strong compatibility evidence; it does not prove the bootloader
already flashed onto a particular device supports rollback. Verify the linked
hook and the actual bootstrap bootloader in the hardware spike.

The proposed wake sequence is:

1. Detect trial state early and reconcile any durable previous update attempt.
2. Load validated configuration, check battery and connect as today.
3. Check the firmware manifest when OTA is enabled, with bounded failure time.
4. Fetch image status and render if changed. Force a real render on a trial boot
   or when the firmware overlay identity has changed.
5. After successful panel refresh, confirm a trial image and check confirmation
   succeeded. Never install another candidate while confirmation is pending.
6. If an eligible offer exists, verify power and compatibility, persist the
   attempt identity, stream into the inactive slot, verify it and reboot.
7. Otherwise follow the existing network shutdown and sleep behavior.

There are two specific control-flow changes:

- [displayImage()](../src/lib.cpp) shuts down MQTT and Wi-Fi *before*
  `board.display()`. Discover the offer earlier and keep networking alive through
  panel refresh only when an eligible update is waiting. Recheck battery before
  writing. Ordinary refreshes can retain the present power-saving shutdown.
- [setup()](../src/src.ino) sleeps immediately when the image signature matches.
  OTA discovery must run before that exit. A confirmed firmware may update with
  its retained valid page, but a trial image must actually download/decode/draw
  before confirmation. Never use the cached PNG signature alone as proof.

Configuration helpers currently sleep internally on SD/parser failures. Include
those paths, critical battery, Wi-Fi failure, image failure and watchdog resets
in trial handling. Refactor terminal decisions enough to make them testable.
Optional MQTT/NTP failures should retain their current nonfatal behavior.
The panel driver returning from `display()` proves completion of the software
path, not optical correctness of the physical display.

For a trial unable to render, use a bounded retry within that boot and then roll
back. Do not leave it pending through routine deep sleep. A transient outage can
therefore reject an otherwise good release; provide an explicit retry mechanism
for a quarantined artifact rather than retrying it automatically every wake.

**Configuration survival is a separate requirement**

Our public release image requires an SD card. Our embedded build compiles the
entire configuration into the application; replacing it with the public SD
image would lose its operating configuration and fail without a card.
Upstream solves only part of this problem by persisting Wi-Fi credentials and
server URL in NVS; our configuration also includes status URL, rotation, refresh
intervals, timezone and MQTT identity/settings. See our
[config loader](../src/config.cpp) and
[upstream settings design](https://github.com/chrisjtwomey/epd/blob/655ed4f99ce06e7a0341fbcc304dc1625f73bee4/docs/ota.md).

For the first OTA release, support SD-configured devices and reject generic
offers for embedded devices. A manually supplied, device-specific embedded
binary is possible, but contains credentials and must not be published or
served from an unrestricted firmware directory.

For generic OTA on installations without SD, add versioned NVS configuration
provisioning in the USB bootstrap. Preserve the full required configuration,
write only on changes, define explicit provisioning/import precedence, and
keep schemas readable by the previous firmware during rollback. Generic
release defaults must never overwrite provisioned values. Budget this as a
separate work item rather than silently changing existing configuration behavior.

**Version overlay**

Use the existing generated `FIRMWARE_VERSION`, already emitted in startup and
WAKE logs. Draw it after successful image decoding and before `board.display()`.
No server image mutation or extra panel refresh is needed. Reserve a small,
legible area, clear its background, bound long development strings, and test all
four supported rotations against actual layouts. Upstream's coordinates and
custom font are design references; they should not be copied blindly.

Track the displayed firmware identity separately from the image SHA. After an
upgrade, an unchanged PNG must not preserve an old label or bypass trial
validation. Keep the server image hash as the hash of the downloaded image;
the device overlay is not part of that artifact. The PWA will consequently not
show the physical panel's label. Optional status-page reporting should use
device diagnostics and a last-seen timestamp, not the server's own build version.

Because e-paper retains pixels, the label means “version used for the last
successful draw,” not “the board is currently online.” A draw followed by a
confirmation failure or rollback can briefly leave the newer label visible;
the next successful old-firmware draw must restore it.

**OTA protocol and artifact policy**

Prefer an optional explicit firmware manifest URL in device configuration.
This avoids having to replace InkplateLibrary's opaque image-download path just
to read response headers, works without `calendar.status_url`, and keeps firmware
availability independent of weather readiness. A single small request per normal
wake is a reasonable initial tradeoff; measure its battery cost before combining
protocols. Missing/disabled OTA endpoints should not fail a weather refresh.

Proposed contract: a versioned endpoint such as `/api/v1/firmware/offer`, returning
204 for no offer or JSON identifying product, hardware, configuration mode,
partition-layout ID, protocol version, firmware version, byte length, SHA-256,
and immutable artifact URL. Client requests provide corresponding capability
and running-version information. Validate compatibility again on the board.
Use a fork-specific product ID: upstream uses `inkplate10-weather-cal`, which
is insufficient to distinguish these now-incompatible forks.

Use `/firmware/<sha256>.bin` or an equivalent immutable URL, fixed Content-Length,
and the headers required by the chosen update client. Keep previously offered
artifacts available for delayed downloads. Atomically publish a desired-version
manifest only after complete validation. Serving must never expose a partially
downloaded file or silently substitute a newer binary for an earlier offer.

Default OTA off, stable releases only, development builds excluded, and no
automatic downgrade. Compare parsed release versions and artifact identities;
our development form is `vX.Y.Z+g<commit>[.dirty]`. Upstream merely tests version
inequality and picks files by modification time, which can offer downgrades.
An intentional downgrade should be an explicit operator action. Confirm that
manifest version matches the built image identity, rather than trusting a filename.

Point release ingestion at `OrangeSpyderMan/inkplate10-weather-cal` and asset
`inkplate10-firmware.bin`, never upstream's repository or `firmware.bin` default.
Only promote releases with a validated OTA compatibility manifest; historic
releases predate the new contract. Preserve our explicit firmware/container
dispatches and `next`/`main` synchronization rules in [releasing.md](releasing.md).

**Specific upstream pitfalls to address**

- **Crash-induced retry loops.** Upstream writes rejection memory only inside
  `otaRollback()`. A panic or watchdog reset before that call can roll back through
  the bootloader without remembering the rejected release. Persist the candidate
  hash, prior identity and expected target slot before boot selection. On the old
  firmware's return, reconcile that record with partition state and quarantine
  failed candidates, distinguishing interrupted downloads from failed trials.
- **Premature confirmation.** Arduino normally confirms before `setup()`.
  Override the hook with correct C linkage and verify the symbol in the build.
  A sketch compiler flag alone cannot retrofit an incompatible bootloader.
- **Offer/download races.** Upstream's `/firmware.bin` means whichever image is
  current at download time. Immutable URLs and retained files remove this race.
- **Failed-fetch ETags.** Upstream stores the release ETag before successfully
  downloading its asset. A subsequent 304 can suppress retry of a failed download.
  Track pending downloads separately or commit the ETag only after successful
  ingestion. Test late assets: our release exists before dispatched builds finish.
- **Artifact replacement.** Our release upload uses `--clobber`; upstream skips
  a tag already held. Detect changed digests for an existing tag, report them,
  and require deliberate promotion rather than silently replacing immutable data.
- **Power and retry budgets.** Upstream uses 20% capacity; we use measured voltage
  with invalid-reading detection. Establish a higher OTA-specific threshold on
  hardware, treat unknown voltage conservatively, and cap transfer attempts and
  total awake time. A per-read timeout alone is not a total transfer deadline.
- **Trust.** Upstream uses plain `WiFiClient` and MD5. That detects transfer errors
  but does not authenticate firmware against a network attacker. A limited LAN
  pilot must explicitly trust that network. For unattended deployment, establish
  authenticated transport or signed-manifest verification with a pinned key and
  a verified payload hash before activation. Stock HTTPUpdate does not by itself
  implement that signed-manifest contract; include a verifier/custom streaming
  path as needed. Never use disabled TLS verification as the solution.
- **Proxy and transport behavior.** Define a device-reachable origin rather than
  constructing URLs from an unchecked Host header. Test HTTP/HTTPS policy,
  redirects, Content-Length, connection interruption and slow clients. SHA-256
  without an authenticated source is also not proof of origin.
- **Dependency scope.** Upstream now depends on IBoard, PlatformIO and epd's server
  lifecycle. Port bounded helpers/tests with MIT attribution; adopting the entire
  framework is a separate decision. Its `^0.2.0` firmware constraints are not exact
  pins despite the release-note wording; preserve our explicit dependency pins.

**Implementation plan and acceptance gates**

The following are proposed independently reviewable changes, in dependency order.
Effort is a planning estimate for one engineer familiar with the project, with a
board and USB access; it excludes waiting for hardware and extended soak time.

| Step | Changes and likely files | Acceptance gate | Estimate |
| --- | --- | --- | --- |
| 1. Overlay | `src/lib.cpp`, version display policy and configuration documentation | Readable in all rotations, bounded dev labels, unchanged-image behavior correct | 0.5–1 day |
| 2. OTA bootstrap spike | Explicit partition option in `Makefile`; partition/size checks in firmware CI; minimal trial updater | Two slots detected; A → B succeeds; B crash returns to A; actual bootloader proven | 1–2 days |
| 3. Artifact service | New firmware store/manifest module; `web.py`, `web_server.py`, configuration and server tests | Disabled/no-offer behavior, capability checks, immutable download, concurrent serving | 1–2 days |
| 4. Device OTA | New OTA module; `src.ino`, `lib.cpp`, `config.*`, config generator/example | SD device updates after display; all trial exits settle; crash quarantine; power/timeout limits; authenticity policy implemented | 2–4 days |
| 5. Release ingestion | Single updater process, container/systemd/installers, release manifest and bootstrap assets | Delayed/failed asset downloads retry; no duplicate pollers; restart retains state; correct fork/tag/build provenance | 1–2 days |
| 6. Validation and rollout | Host state-machine tests, `tools/hardware_soak.py`, release/operations documentation | Fault matrix below passes; bootstrap and USB recovery rehearsed; opt-in canary completes soak | 2–3 days |
| 7. Generic no-SD support | Versioned NVS provisioning and compatible public firmware | Embedded installation survives generic update and rollback with all settings intact | Additional 2–4 days |

Allow roughly **8–14 engineering days for an SD-first unattended implementation**,
plus soak time; NVS provisioning adds roughly 2–4 days. Signing/key-management or
bootloader changes beyond the existing core may expand that range. Step 2 is the
go/no-go gate before committing to the larger estimate. Steps 3 and release
artifact preparation can proceed independently once the protocol is agreed.

Required validation includes semantic behavior, not only source-string tests:

- Host tests for update eligibility, no downgrades, wrong fork/board/config/layout,
  malformed or oversized manifest, length/hash/signature mismatch, and rejected
  candidate handling across simulated resets.
- Server tests for two Gunicorn workers reading during publication, interrupted
  staging, slow binary clients alongside PNG/health requests, persistent restart,
  missing assets, GitHub 304/retry behavior and manifest/artifact consistency.
- CI builds of SD and embedded modes, explicit partition decoding, app-size limits,
  rollback-hook linkage, and release manifest identity matching the tagged source.
- Hardware A → B → C upgrades, rejected B followed by fixed C, interrupted writes,
  power loss after boot selection, crash before setup, configuration failure,
  Wi-Fi/image failure on trial, failed confirmation, low/invalid battery, unchanged
  PNG, normal deep sleep and recovery to the previous firmware.
- Measure idle-wake overhead and OTA energy; verify normal no-update sleep current
  and radio-off timing. Test the specific Inkplate revisions intended for support.

Roll out the overlay first if desired, then the server with OTA disabled. USB
bootstrap one canary, manually promote a known test artifact, prove rollback and
recovery, and only then enable automatic release ingestion for opted-in devices.
Keep a documented way to stop new offers and clear a specific quarantine. USB
recovery remains necessary for hardware faults or loss of both bootable images.
