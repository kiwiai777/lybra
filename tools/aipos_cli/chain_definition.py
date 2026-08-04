"""AIPOS-324 S2 — Task Chain Definition Interface

Defines mechanical step sequences for different task types, enabling non-code tasks
to have different closure flows (e.g., no git push, different audit routes).

This interface prepares for AIPOS-304 two-layer schema: task chains can be loaded
from configuration files or governance records rather than hardcoded in pump logic.

Current default: code task chain (RETURN → dispatch_audit → wait_verdict → finalize → close)
Future: validation chains, research chains, infrastructure chains, etc.
"""
from __future__ import annotations

from typing import Protocol


class TaskChain(Protocol):
    """Protocol for task chain definitions.
    
    A task chain defines the sequence of mechanical steps after an agent returns work.
    Each step can be automated (pump executes) or manual (advisor judgment).
    """
    
    def get_steps_after_return(self, task_mode: str, task_class: str) -> list[str]:
        """Return ordered list of mechanical step names after RETURN artifact detected.
        
        Args:
            task_mode: Task mode (code, validation, research, etc.)
            task_class: Task class (simple, complex)
        
        Returns:
            List of step names: ["dispatch_audit", "wait_verdict", "finalize", "close"]
        """
        ...
    
    def is_step_automated(self, step_name: str) -> bool:
        """Check if a step should be automated by pump vs manual advisor action.
        
        Returns:
            True if pump should execute, False if advisor judgment required
        """
        ...


class CodeTaskChain:
    """Default chain for code tasks (current implementation).
    
    Steps:
    1. dispatch_audit: Send to auditor (automated)
    2. wait_verdict: Wait for audit verdict (automated monitoring)
    3. finalize: Git commit+push if PASS (automated with doorway verification)
    4. close: Move to completed/ (automated)
    
    Non-automated (advisor judgment):
    - Review audit FAIL/REQUEST_CHANGES (advisor decides: fix vs withdraw)
    - Owner verify收编 (advisor presents, owner approves)
    - Governance authoring (advisor writes decision logs, not ghostwritten)
    """
    
    def get_steps_after_return(self, task_mode: str, task_class: str) -> list[str]:
        """Code task standard sequence."""
        if task_mode != "code":
            # Non-code tasks: placeholder for future chains
            return ["dispatch_validation", "wait_verdict", "close"]
        
        # Code task: full sequence
        steps = [
            "dispatch_audit",      # Send to auditor
            "wait_verdict",        # Monitor for verdict record
            "finalize_if_pass",    # Git operations (only if PASS)
            "close",               # Move to completed/
        ]
        
        return steps
    
    def is_step_automated(self, step_name: str) -> bool:
        """All listed steps are automated mechanical steps.
        
        Advisor judgment steps are NOT in this list:
        - review_failure (advisor decides fix/withdraw)
        - owner_verify (advisor presents, owner approves)
        - governance_authoring (advisor writes, not ghostwritten)
        """
        automated_steps = {
            "dispatch_audit",
            "dispatch_validation",
            "wait_verdict",
            "finalize_if_pass",
            "close",
        }
        return step_name in automated_steps


class ValidationTaskChain:
    """Chain for validation/verification tasks (no git operations).
    
    Steps:
    1. dispatch_validation: Send to validation platform (automated)
    2. wait_verdict: Wait for validation result (automated)
    3. close: Archive result (automated)
    
    No git push, no artifact ingestion to product repo.
    """
    
    def get_steps_after_return(self, task_mode: str, task_class: str) -> list[str]:
        return [
            "dispatch_validation",
            "wait_verdict",
            "close",
        ]
    
    def is_step_automated(self, step_name: str) -> bool:
        automated_steps = {
            "dispatch_validation",
            "wait_verdict",
            "close",
        }
        return step_name in automated_steps


def get_chain_for_task(task_mode: str, task_class: str = "simple") -> TaskChain:
    """Factory: return appropriate chain for task type.
    
    Args:
        task_mode: code, validation, research, etc.
        task_class: simple, complex
    
    Returns:
        TaskChain implementation
    
    Future: Load from configuration file (AIPOS-304 two-layer schema).
    """
    if task_mode == "code":
        return CodeTaskChain()
    elif task_mode == "validation":
        return ValidationTaskChain()
    else:
        # Default fallback: code chain
        # TODO: AIPOS-304 will add research chain, infrastructure chain, etc.
        return CodeTaskChain()
