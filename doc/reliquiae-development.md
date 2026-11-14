# Reliquiae development build

This branch advances the original upstream snapshot to Elements 23.3.4
(`ca172806f66f03712b1050f7ee0020fd588a30bb`) and carries the security and build
changes listed below. Upstream history and the MIT licence are retained.
The daemon retains the upstream numeric version; identify this maintenance build
by its repository commit and verified source/binary hash manifest, rather than
assuming that every binary reporting 23.3.4 includes these additional backports.

This is a development distribution. It does not introduce a production Reliquiae
network, establish a federation, or supply a deployed Bitcoin reserve/withdrawal
service. Specify the chain explicitly when running a daemon. Use isolated regtest
for local development and do not fund it with real assets.

## Security changes

Elements 23.3.4 includes the upstream proof-cache hardening and regression tests
from [Elements PR #1600](https://github.com/ElementsProject/elements/pull/1600).
Additional adaptations in this branch address:

| Issue | Change | Upstream reference |
| --- | --- | --- |
| CVE-2026-117350 | Respect receive backpressure when selecting sockets for exceptional-data handling | [Bitcoin #36665](https://github.com/bitcoin/bitcoin/pull/36665) |
| CVE-2026-117349 | Track send-pause transitions and disconnect peers that remain paused beyond the timeout | [Bitcoin #36667](https://github.com/bitcoin/bitcoin/pull/36667) |
| CVE-2026-117346 | Use 64-bit block/undo positions and sizes with matching seek, truncate and allocation handling | [Bitcoin #36669](https://github.com/bitcoin/bitcoin/pull/36669) |
| CVE-2025-62859 | Reject witness-malleated compact-block reconstruction before completing the download request, preserving Elements-specific witness rules | [Bitcoin #32646](https://github.com/bitcoin/bitcoin/pull/32646) |
| CVE-2024-52911 | Construct transaction data before the script-check controller so queued checks finish before referenced data is destroyed on early returns | [Bitcoin #31112](https://github.com/bitcoin/bitcoin/pull/31112) |
| CVE-2024-52922 | Permit bounded parallel compact-block requests with per-peer cleanup and an outbound-reserved reconstruction slot | [Bitcoin #27626](https://github.com/bitcoin/bitcoin/pull/27626) |

The inspected Tor-whitelist and variable-length Schnorr-message fixes are already
present in the selected release. This change is a bounded maintenance update,
not a claim that all possible security defects or dependency issues are resolved.
The separate Bitcoin backend and any Tor service need their own maintained builds.

Old representable disk positions retain their existing variable-integer encoding.
After writing offsets that require more than 32 bits, downgrading to older code
is not supported. Operating-system/filesystem limits, including 32-bit and FAT32
limits, are not removed by widening the internal fields.

Public proof-of-work operation has not been qualified by this maintenance work.
The header-index memory advisory CVE-2019-25220 needs a mode-specific assessment;
`trim_headers` does not establish an aggregate memory bound. Threshold-signed
headers constrain unauthenticated forgery only when the signing policy actually
requires independent signatures. An `OP_TRUE` test chain does not provide that
protection and must remain isolated. See the [upstream advisory](https://bitcoincore.org/en/2024/09/18/disclose-headers-oom/).

## Build

See the upstream [macOS guide](build-osx.md), [Unix guide](build-unix.md) and
[pinned dependency build](../depends/README.md). The dependency recipes pin
Boost 1.81.0 and Berkeley DB 4.8.30 with SHA-256 checksums. Legacy BDB support is
used to exercise the upstream wallet tests; descriptor wallets use SQLite.
Do not assume the latest Homebrew Boost is compatible with this release.

The locally verified macOS ARM64 configuration used Apple Clang 21, autoconf 2.73,
automake 1.20, libtool 2.6.2, pkg-config 3.0.10, libevent and SQLite. After preparing
Boost and the BDB prefix using the upstream dependency instructions, configure
with these paths set to the local installations:

```sh
./autogen.sh
./configure --with-gui=no --disable-bench \
  --with-boost="$BOOST_ROOT" --with-sqlite=yes \
  --without-miniupnpc --without-natpmp --disable-zmq \
  --disable-man --disable-ccache \
  "BDB_LIBS=-L$BDB_ROOT/lib -ldb_cxx-4.8" \
  "BDB_CFLAGS=-I$BDB_ROOT/include" \
  'CXXFLAGS=-O1 -g0'
make -j4
```

The build fixes add the required libevent include flags to wallet, utility and
fuzz targets. They do not disable those targets to bypass missing headers.

## Verification

The macOS ARM64 maintenance build passed:

- Wallet-enabled daemon, CLI, transaction/wallet tools and fuzz executable build.
- 58 focused security, script-check queue and validation test cases with 34,075
  assertions, including proof-cache, send/receive backpressure, sparse files
  beyond 4 GiB and witness reconstruction.
- Full `make check`.
- Confidential transaction/wallet recovery, dynamic federation, three federated
  peg variants, block validation and the complete compact-block functional script.
- The block-validation and compact-block runs explicitly used four script-check
  threads; the compact-block regressions cover parallel completion, outbound-slot
  reservation and survival of another peer's disconnection.
- Supplemental RPC block fetching, high-bandwidth compact blocks, blocks-only
  compact blocks and unrequested-block handling functional scripts.

Repeat the checks against the current source:

```sh
make check
./src/test/test_bitcoin --run_test=sigcache_tests,net_tests,flatfile_tests,blockencodings_tests,checkqueue_tests,validation_block_tests,validation_chainstate_tests,validation_chainstatemanager_tests,validation_tests
python3 test/functional/test_runner.py p2p_compactblocks.py \
  feature_confidential_transactions.py feature_dynafed.py feature_fedpeg.py
```

Use the runner's help for variant-specific options and fresh temporary paths.
These tests used an Elements-regtest parent. They do not demonstrate a production
Bitcoin Core/Watchman bridge. Windows, Linux, 32-bit and runtime TCP urgent-data
attack reproduction were not exercised by the local macOS run.

## Local demo

`contrib/reliquiae/regtest_demo.py` provides a separate no-value, loopback-only
confidential-transfer demonstration. It creates fresh temporary state, stops only
its own daemons and does not use existing wallets. Run it against the built tools:

```sh
python3 contrib/reliquiae/regtest_demo.py --bindir ./src
```

The verified run confirmed a 1.25-unit confidential payment to a descriptor
recipient through actual local P2P relay and block confirmation. Its synthetic
genesis funding wallet uses legacy BDB; it first moves funding to standard
outputs without relaxing receiver policy. Parent peg validation is disabled in
this standalone regtest demonstration, so no Bitcoin deposit/withdrawal claim is
made. Read its help and result file for limitations. It is not a production
network launcher.
