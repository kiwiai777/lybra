#!/usr/bin/env python3
"""Manual verification script for AIPOS-366: claim-before-work hard enforcement.

This script tests the live gate to verify:
1. include_body is denied without a claim record (CLAIM_REQUIRED)
2. include_body is allowed after claiming
3. Metadata access works without claim
"""
import json
import requests
import sys
from pathlib import Path

def load_connection():
    """Load connection.json to get gate URL and tokens."""
    conn_path = Path.home() / "ai-project-os/2_projects/lybra/.lybra/connection.json"
    with open(conn_path) as f:
        return json.load(f)

def call_mcp_tool(gate_url, token, tool_name, arguments):
    """Call an MCP tool via HTTP."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments
        }
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
    
    response = requests.post(f"{gate_url}/mcp", json=payload, headers=headers, timeout=10)
    return response.json()

def main():
    print("=== AIPOS-366 Manual Verification ===\n")
    
    # Load connection
    conn = load_connection()
    gate_url = conn["mcp"]["rpc_url"].replace("/mcp", "")
    
    # Find executor token
    exec_token = None
    for t in conn["tokens"]:
        if t.get("role") == "executor":
            exec_token = t["token"]
            print(f"✓ Found executor token (fingerprint: {t.get('fingerprint')})")
            break
    
    if not exec_token:
        print("✗ No executor token found")
        return 1
    
    # Test task: use AIPOS-366 itself (already claimed)
    task_id = "AIPOS-366"
    actor = "exec.lybra.kiwiai-dev"
    
    print(f"\nTest task: {task_id}")
    print(f"Actor: {actor}")
    print(f"Gate: {gate_url}\n")
    
    # Test 1: Metadata without include_body (should work)
    print("Test 1: Metadata access without include_body...")
    response = call_mcp_tool(gate_url, exec_token, "lybra_task_preview", {
        "task_id": task_id,
        "include_body": False,
        "actor": actor
    })
    
    if "result" in response and "structuredContent" in response["result"]:
        structured = response["result"]["structuredContent"]
        if structured.get("ok"):
            print("  ✓ Metadata access allowed (as expected)")
        else:
            print(f"  ✗ Unexpected error: {structured.get('error_code')}")
    else:
        print(f"  ✗ Unexpected response format")
    
    # Test 2: Body access with include_body (should work since task is claimed)
    print("\nTest 2: Body access with include_body on claimed task...")
    response = call_mcp_tool(gate_url, exec_token, "lybra_task_preview", {
        "task_id": task_id,
        "include_body": True,
        "actor": actor
    })
    
    if "result" in response and "structuredContent" in response["result"]:
        structured = response["result"]["structuredContent"]
        if structured.get("ok"):
            if "body_markdown" in structured.get("data", {}):
                print("  ✓ Body access allowed with valid claim (as expected)")
                body_preview = structured["data"]["body_markdown"][:100]
                print(f"  ✓ Body preview: {body_preview}...")
            else:
                print("  ✗ No body_markdown in response")
        else:
            error_code = structured.get("error_code")
            if error_code == "CLAIM_REQUIRED":
                print("  ⚠ Body access denied (CLAIM_REQUIRED)")
                print("    This is expected if the claim record doesn't match")
            else:
                print(f"  ✗ Unexpected error: {error_code}")
    else:
        print(f"  ✗ Unexpected response format")
    
    # Test 3: Body access on a pending task (should be denied)
    print("\nTest 3: Body access on pending task without claim...")
    # Try to find a pending task
    pending_response = call_mcp_tool(gate_url, exec_token, "lybra_queue_status", {})
    
    pending_task = None
    if "result" in pending_response and "structuredContent" in pending_response["result"]:
        structured = pending_response["result"]["structuredContent"]
        if structured.get("ok"):
            pending_tasks = structured.get("data", {}).get("pending", [])
            if pending_tasks:
                pending_task = pending_tasks[0].get("task_id")
    
    if pending_task:
        print(f"  Testing with pending task: {pending_task}")
        response = call_mcp_tool(gate_url, exec_token, "lybra_task_preview", {
            "task_id": pending_task,
            "include_body": True,
            "actor": actor
        })
        
        if "result" in response and "structuredContent" in response["result"]:
            structured = response["result"]["structuredContent"]
            if not structured.get("ok") and structured.get("error_code") == "CLAIM_REQUIRED":
                print("  ✓ Body access denied (CLAIM_REQUIRED) on unclaimed task (as expected)")
            else:
                print(f"  ✗ Unexpected result: ok={structured.get('ok')}, error={structured.get('error_code')}")
        else:
            print(f"  ✗ Unexpected response format")
    else:
        print("  ⚠ No pending tasks found to test")
    
    print("\n=== Verification Complete ===")
    return 0

if __name__ == "__main__":
    sys.exit(main())
