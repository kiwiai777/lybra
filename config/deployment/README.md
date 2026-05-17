# Deployment Config Examples

This directory contains examples only.

AIPOS-84 does not install, enable, or start services. Copy these files into host-local private configuration only after Owner approval of the target host and access method.

Files:

- `lybra-board.example.env`: example environment file for an owner-only Board service.
- `lybra-board.example.service`: example systemd unit for a localhost-bound Board service.

The service must bind to `127.0.0.1` unless Owner explicitly approves a different private access boundary.
