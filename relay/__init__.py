"""Lybra relay — the external-communication subsystem (对外沟通关口的中转), stage 1: receive-only probe.

Boundary (enforced by tests/test_aipos_f290_relay_boundary.py):
  - separate process (`python3 -m relay.probe`), separate credentials (<HOME>/.config/lybra/relay/<channel>.env);
  - the gate core (tools/, web/) never imports relay; relay imports nothing from tools/ or web/ and calls no gate verb;
  - standard library only (no runtime dependency added to Lybra).
Channel adapters live in relay/channels/ (WeCom smart bot long connection, Weixin ClawBot); a new channel = one
entry in relay.config.CHANNELS + one adapter module.
"""
