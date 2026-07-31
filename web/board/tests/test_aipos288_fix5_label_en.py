"""
AIPOS-288 FIX-5: board_config label_en contract test.

Verifies:
  a) Frontend renders workspace label with EN preference (label_en > label fallback)
  b) Server-side init writes label_en when provided
  c) Backward compatibility: workspaces without label_en still render correctly
  d) All 4 render points use the same label logic (overview list, detail H1, portal, browser title)
"""

import json
import re
from pathlib import Path


WEB_BOARD_ROOT = Path(__file__).parent.parent
OVERVIEW_HTML = WEB_BOARD_ROOT / "static" / "overview.html"
DETAIL_HTML = WEB_BOARD_ROOT / "static" / "project-detail.html"
APP_PY = WEB_BOARD_ROOT / "app.py"
I18N_JS = WEB_BOARD_ROOT / "static" / "i18n.js"


def test_workspace_label_helper_exists():
    """
    AIPOS-288 FIX-5a: Both HTML files must define workspaceLabel(workspace) helper.
    
    Helper logic: EN mode prefers label_en, fallback to label.
    """
    for html_path in [OVERVIEW_HTML, DETAIL_HTML]:
        if not html_path.exists():
            raise AssertionError(f"{html_path.name} not found")
        
        content = html_path.read_text(encoding='utf-8')
        
        # Check for workspaceLabel function
        if not re.search(r'function\s+workspaceLabel\s*\(\s*workspace\s*\)', content):
            raise AssertionError(
                f"AIPOS-288 FIX-5a: workspaceLabel(workspace) helper not found in {html_path.name}"
            )
        
        # Check for label_en preference logic
        if "workspace.label_en" not in content:
            raise AssertionError(
                f"AIPOS-288 FIX-5a: workspaceLabel must check workspace.label_en in {html_path.name}"
            )
        
        # Check for fallback to label
        if not re.search(r'workspace\.label\s*\|\|', content):
            raise AssertionError(
                f"AIPOS-288 FIX-5a: workspaceLabel must fallback to workspace.label in {html_path.name}"
            )


def test_overview_list_uses_helper():
    """
    AIPOS-288 FIX-5a: overview.html workspace card must use workspaceLabel(workspace).
    """
    if not OVERVIEW_HTML.exists():
        raise AssertionError("overview.html not found")
    
    content = OVERVIEW_HTML.read_text(encoding='utf-8')
    
    # Find createWorkspaceCard function
    match = re.search(
        r'function\s+createWorkspaceCard\s*\([^)]*\)\s*\{(.*?)(?=\n\s{4}function|\n\s{2}</script>)',
        content,
        re.DOTALL
    )
    if not match:
        raise AssertionError("AIPOS-288 FIX-5a: createWorkspaceCard function not found")
    
    func_body = match.group(1)
    
    # Check that workspaceLabel is called (not direct workspace.label access)
    if "workspaceLabel(workspace)" not in func_body:
        raise AssertionError(
            "AIPOS-288 FIX-5a: createWorkspaceCard must call workspaceLabel(workspace)"
        )
    
    # Ensure no direct workspace.label assignment in label rendering
    # (Allow workspace.label in other contexts like error handling)
    if re.search(r'label\.textContent\s*=\s*workspace\.label', func_body):
        raise AssertionError(
            "AIPOS-288 FIX-5a: createWorkspaceCard should not directly assign workspace.label; use workspaceLabel()"
        )


def test_detail_renders_uses_helper():
    """
    AIPOS-288 FIX-5a: project-detail.html must use workspaceLabel() in 3 places:
      - createProjectHeader (H1)
      - renderPortalHeader (portal card title)
      - browser title (document.title)
    """
    if not DETAIL_HTML.exists():
        raise AssertionError("project-detail.html not found")
    
    content = DETAIL_HTML.read_text(encoding='utf-8')
    
    # 1) Check createProjectHeader uses workspaceLabel
    header_match = re.search(
        r'function\s+createProjectHeader\s*\([^)]*\)\s*\{(.*?)(?=\n\s{4}function)',
        content,
        re.DOTALL
    )
    if not header_match:
        raise AssertionError("AIPOS-288 FIX-5a: createProjectHeader function not found")
    
    if "workspaceLabel(workspace)" not in header_match.group(1):
        raise AssertionError(
            "AIPOS-288 FIX-5a: createProjectHeader must call workspaceLabel(workspace)"
        )
    
    # 2) Check renderPortalHeader uses workspaceLabel
    portal_match = re.search(
        r'function\s+renderPortalHeader\s*\([^)]*\)\s*\{(.*?)(?=\n\s{4}function)',
        content,
        re.DOTALL
    )
    if not portal_match:
        raise AssertionError("AIPOS-288 FIX-5a: renderPortalHeader function not found")
    
    if "workspaceLabel(workspace)" not in portal_match.group(1):
        raise AssertionError(
            "AIPOS-288 FIX-5a: renderPortalHeader must call workspaceLabel(workspace)"
        )
    
    # 3) Check document.title uses workspaceLabel
    # Look for document.title assignment after workspace data is loaded
    title_pattern = r'document\.title\s*=\s*[^;]*workspaceLabel\s*\(\s*workspace\s*\)'
    if not re.search(title_pattern, content):
        raise AssertionError(
            "AIPOS-288 FIX-5a: document.title must use workspaceLabel(workspace)"
        )


def test_server_init_accepts_label_en():
    """
    AIPOS-288 FIX-5b: _workspace_init_route must accept label_en and write to board_config.
    """
    if not APP_PY.exists():
        raise AssertionError("app.py not found")
    
    content = APP_PY.read_text(encoding='utf-8')
    
    # Find _workspace_init_route function
    match = re.search(
        r'def\s+_workspace_init_route\s*\([^)]*\)\s*->.*?:\s*(.*?)(?=\ndef\s+)',
        content,
        re.DOTALL
    )
    if not match:
        raise AssertionError("AIPOS-288 FIX-5b: _workspace_init_route function not found")
    
    func_body = match.group(1)
    
    # Check label_en extraction from payload
    if not re.search(r'label_en\s*=.*payload\.get\(["\']label_en["\']', func_body):
        raise AssertionError(
            "AIPOS-288 FIX-5b: _workspace_init_route must extract label_en from payload"
        )
    
    # Check label_en is written to workspace entry (either dict literal or subscript assignment)
    has_label_en_write = (
        re.search(r'["\']label_en["\']\s*:\s*label_en', func_body) or
        re.search(r'ws_entry\[["\']label_en["\']\]\s*=\s*label_en', func_body)
    )
    if not has_label_en_write:
        raise AssertionError(
            "AIPOS-288 FIX-5b: _workspace_init_route must write label_en to workspace entry"
        )


def test_frontend_wizard_sends_label_en():
    """
    AIPOS-288 FIX-5b: overview.html serverSideInit must send label_en in payload.
    """
    if not OVERVIEW_HTML.exists():
        raise AssertionError("overview.html not found")
    
    content = OVERVIEW_HTML.read_text(encoding='utf-8')
    
    # Find serverSideInit function
    match = re.search(
        r'async\s+function\s+serverSideInit\s*\([^)]*\)\s*\{(.*?)(?=\n\s{4}addProjectBtn\.addEventListener)',
        content,
        re.DOTALL
    )
    if not match:
        raise AssertionError("AIPOS-288 FIX-5b: serverSideInit function not found")
    
    func_body = match.group(1)
    
    # Check for label_en input extraction
    if "projectNameEnInput" not in func_body:
        raise AssertionError(
            "AIPOS-288 FIX-5b: serverSideInit must read projectNameEnInput"
        )
    
    # Check for label_en in payload
    if not re.search(r'payload\.label_en\s*=', func_body):
        raise AssertionError(
            "AIPOS-288 FIX-5b: serverSideInit must include label_en in API payload"
        )


def test_i18n_keys_for_wizard():
    """
    AIPOS-288 FIX-5b: i18n.js must have translation keys for English name input.
    """
    if not I18N_JS.exists():
        raise AssertionError("i18n.js not found")
    
    content = I18N_JS.read_text(encoding='utf-8')
    
    required_keys = [
        'overview.new_project_modal.project_name_en',
        'overview.new_project_modal.project_name_en_hint'
    ]
    
    for key in required_keys:
        if f"'{key}'" not in content and f'"{key}"' not in content:
            raise AssertionError(
                f"AIPOS-288 FIX-5b: i18n.js missing translation key: {key}"
            )


def test_backward_compatibility():
    """
    AIPOS-288 FIX-5c: Ensure workspaces without label_en still render correctly.
    
    Tests that the fallback logic allows:
      - workspace with only 'label' field
      - workspace with both 'label' and 'label_en'
    """
    # This is a static contract check - runtime behavior verified by:
    # 1. workspaceLabel helper checks label_en existence before accessing
    # 2. Fallback to workspace.label || 'Unnamed Workspace'
    
    for html_path in [OVERVIEW_HTML, DETAIL_HTML]:
        content = html_path.read_text(encoding='utf-8')
        
        # Ensure conditional check for label_en (not direct access that would fail on missing key)
        helper_match = re.search(
            r'function\s+workspaceLabel\s*\([^)]*\)\s*\{(.*?)(?=\n\s{4}function|\n\s{2}//)',
            content,
            re.DOTALL
        )
        if not helper_match:
            continue
        
        helper_body = helper_match.group(1)
        
        # Must check existence before accessing (either && or optional chaining)
        has_safe_check = (
            'workspace.label_en' in helper_body and
            ('&&' in helper_body or 'workspace?.label_en' in helper_body or
             'if (lang === \'en\' && workspace.label_en)' in helper_body)
        )
        
        if not has_safe_check:
            raise AssertionError(
                f"AIPOS-288 FIX-5c: workspaceLabel in {html_path.name} must safely check label_en existence"
            )


def test_api_overview_transparently_returns_label_en():
    """
    AIPOS-288 FIX-6: /api/overview must transparently pass label_en from board_config to response.
    
    Contract: For each workspace in board_config.workspaces:
      - If workspace has label_en key => API response includes label_en
      - If workspace lacks label_en => API response omits label_en (or null)
    
    This test inspects the three assembly points in get_overview():
      1. Error branch (L147): validation failure case
      2. OK branch (L205): successful workspace aggregation
      3. Exception branch (L218): exception handling
    """
    if not APP_PY.exists():
        raise AssertionError("app.py not found")
    
    content = APP_PY.read_text(encoding='utf-8')
    
    # Find get_overview function
    match = re.search(
        r'def\s+get_overview\s*\([^)]*\)\s*->.*?:\s*(.*?)(?=\ndef\s+)',
        content,
        re.DOTALL
    )
    if not match:
        raise AssertionError("AIPOS-288 FIX-6: get_overview function not found")
    
    func_body = match.group(1)
    
    # Verify all three assembly points extract label_en from ws_config and conditionally add to result
    
    # Pattern 1: Extract label_en from ws_config
    label_en_extraction = re.search(r'label_en\s*=\s*ws_config\.get\(["\']label_en["\']\)', func_body)
    if not label_en_extraction:
        raise AssertionError(
            "AIPOS-288 FIX-6: get_overview must extract label_en from ws_config"
        )
    
    # Count occurrences - should appear 3 times (once per assembly point)
    extraction_count = len(re.findall(r'label_en\s*=\s*ws_config\.get\(["\']label_en["\']\)', func_body))
    if extraction_count < 3:
        raise AssertionError(
            f"AIPOS-288 FIX-6: Expected label_en extraction at 3 assembly points, found {extraction_count}"
        )
    
    # Pattern 2: Conditional assignment to result dict
    conditional_writes = re.findall(
        r'if\s+label_en:\s*[\w_]+\[["\']label_en["\']\]\s*=\s*label_en',
        func_body
    )
    if len(conditional_writes) < 3:
        raise AssertionError(
            f"AIPOS-288 FIX-6: Expected conditional label_en write at 3 assembly points, found {len(conditional_writes)}"
        )
    
    # Verify the three specific assembly points:
    # 1. Error branch (has_workspace_queue validation failure)
    error_branch_match = re.search(
        r'if not has_workspace_queue\(root\):.*?error_entry.*?label_en = ws_config\.get\(["\']label_en["\']\).*?if label_en:.*?error_entry\[["\']label_en["\']\] = label_en.*?results\.append\(error_entry\)',
        func_body,
        re.DOTALL
    )
    if not error_branch_match:
        raise AssertionError(
            "AIPOS-288 FIX-6: Error assembly point (validation failure) must transparently pass label_en"
        )
    
    # 2. OK branch (successful aggregation)
    ok_branch_match = re.search(
        r'ok_entry\s*=\s*\{.*?["\']status["\']:\s*["\']ok["\'].*?\}.*?label_en = ws_config\.get\(["\']label_en["\']\).*?if label_en:.*?ok_entry\[["\']label_en["\']\] = label_en.*?results\.append\(ok_entry\)',
        func_body,
        re.DOTALL
    )
    if not ok_branch_match:
        raise AssertionError(
            "AIPOS-288 FIX-6: OK assembly point (successful aggregation) must transparently pass label_en"
        )
    
    # 3. Exception branch
    exception_branch_match = re.search(
        r'except\s+Exception.*?exception_entry\s*=\s*\{.*?["\']error["\'].*?\}.*?label_en = ws_config\.get\(["\']label_en["\']\).*?if label_en:.*?exception_entry\[["\']label_en["\']\] = label_en.*?results\.append\(exception_entry\)',
        func_body,
        re.DOTALL
    )
    if not exception_branch_match:
        raise AssertionError(
            "AIPOS-288 FIX-6: Exception assembly point must transparently pass label_en"
        )


if __name__ == "__main__":
    test_workspace_label_helper_exists()
    test_overview_list_uses_helper()
    test_detail_renders_uses_helper()
    test_server_init_accepts_label_en()
    test_frontend_wizard_sends_label_en()
    test_i18n_keys_for_wizard()
    test_backward_compatibility()
    test_api_overview_transparently_returns_label_en()
    print("✓ All AIPOS-288 FIX-5 + FIX-6 contract tests passed")
