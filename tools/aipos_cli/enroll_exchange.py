#!/usr/bin/env python3
"""AIPOS-362 — Remote agent enrollment exchange tool.

This tool runs on the REMOTE agent machine (Mac, etc.) to exchange an enrollment code
for a capability token. The token is written to a local credential file with 0600 permissions.

Usage:
    python3 enroll_exchange.py <enrollment_code> --gate-url <url> --output <path>

Example:
    python3 enroll_exchange.py "ABC123..." --gate-url http://kiwiai-dev.tail6b5218.ts.net:7118 --output ~/.lybra/credentials.json

Security:
  - The enrollment code is NOT logged.
  - The token value is NOT logged or printed to stdout.
  - The output file is created with 0600 permissions (owner read/write only).
  - The enrollment code is single-use (cannot be reused after exchange).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    import urllib.request
    import urllib.error
except ImportError:
    print("Error: This script requires Python 3.x with urllib", file=sys.stderr)
    sys.exit(1)


def exchange_enrollment_code(gate_url: str, code: str) -> dict[str, Any]:
    """Call the gate's lybra_roles_enroll_exchange MCP verb.
    
    Args:
        gate_url: Gate MCP URL (e.g., http://host:7118)
        code: Enrollment code
    
    Returns:
        MCP response dict
    
    Raises:
        RuntimeError: If the exchange fails
    """
    url = f"{gate_url.rstrip('/')}/mcp"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "lybra_roles_enroll_exchange",
            "arguments": {"code": code}
        }
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
        method='POST'
    )
    
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='replace')
        raise RuntimeError(f"HTTP {e.code}: {error_body}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Failed to connect to gate: {e.reason}")
    except Exception as e:
        raise RuntimeError(f"Exchange failed: {e}")
    
    # Parse MCP response
    if "error" in result:
        error_msg = result["error"].get("message", str(result["error"]))
        raise RuntimeError(f"Gate returned error: {error_msg}")
    
    if "result" not in result:
        raise RuntimeError("Invalid MCP response: missing 'result'")
    
    return result["result"]


def write_credential_file(path: Path, token_entry: dict[str, Any]) -> None:
    """Write the token entry to a credential file with 0600 permissions.
    
    Args:
        path: Output file path
        token_entry: Token entry dict (contains 'token', 'role', 'scopes', etc.)
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Write with 0600 permissions
    path.write_text(json.dumps(token_entry, indent=2, sort_keys=True) + "\n", encoding='utf-8')
    path.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Exchange an enrollment code for a capability token (AIPOS-362)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("code", help="Enrollment code (provided by owner/advisor)")
    parser.add_argument("--gate-url", required=True, help="Gate MCP URL (e.g., http://host:7118)")
    parser.add_argument("--output", required=True, help="Output credential file path (will be created with 0600)")
    parser.add_argument("--quiet", action="store_true", help="Suppress non-error output")
    
    args = parser.parse_args()
    
    output_path = Path(args.output).expanduser().resolve()
    
    if not args.quiet:
        print(f"Exchanging enrollment code with gate: {args.gate_url}")
        print(f"Output credential file: {output_path}")
    
    try:
        result = exchange_enrollment_code(args.gate_url, args.code)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    
    if not result.get("ok"):
        print(f"Error: Exchange failed: {result.get('message', 'Unknown error')}", file=sys.stderr)
        return 1
    
    token_entry = result.get("token_entry")
    if not token_entry:
        print("Error: No token_entry in response", file=sys.stderr)
        return 1
    
    try:
        write_credential_file(output_path, token_entry)
    except Exception as e:
        print(f"Error writing credential file: {e}", file=sys.stderr)
        return 1
    
    if not args.quiet:
        print(f"✓ Successfully exchanged enrollment code")
        print(f"  Role: {token_entry.get('role', 'unknown')}")
        if token_entry.get("agent_instance"):
            print(f"  Instance: {token_entry['agent_instance']}")
        print(f"  Scopes: {', '.join(token_entry.get('scopes', []))}")
        print(f"  Fingerprint: {token_entry.get('fingerprint', 'unknown')}")
        print(f"\n  Credential saved to: {output_path} (0600)")
        print(f"\n⚠ The enrollment code has been consumed and cannot be reused.")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
