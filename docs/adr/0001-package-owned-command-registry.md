# Package owns executable command contracts

`com.zh1zh1.csharpconsole` is the source of truth for canonical built-in and custom
execution contracts, served as a Registry Snapshot over the live command transport;
`unity-cli` owns only its Routing Overlay and machine-local Command Cache. Agent
discovery merges those sources into Route Cards and Canonical Command Contracts,
avoiding a second hand-maintained schema while preserving offline planning. The
cache refreshes through a conditional snapshot request keyed by an opaque registry
generation token: an unchanged answer transfers no contracts.
