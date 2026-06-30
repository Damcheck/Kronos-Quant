"""Untrusted-origin (imported / shared) strategy package — SANDBOX-ONLY.

Strategy modules placed here come from OUTSIDE this machine (a shared export
imported via the strategy sharing feature). Their author-controlled Python is
**never imported into the trusted parent process**: the in-process registry
discovery, intake, optimizer/backtest fallbacks, and the auto-intake scheduler all
scan ONLY ``forven.strategies.custom`` — so files in this package are invisible to
them by construction. They are imported and executed exclusively inside the
locked-down subprocess worker (``forven.sandbox.strategy_worker``), which runs with
a secret-free environment, network denied, filesystem confined, and resource caps.

See docs/strategy-share-security-audit-2026-06-29.md (R2). The package marker is
committed; the strategy modules inside remain gitignored local files.
"""
