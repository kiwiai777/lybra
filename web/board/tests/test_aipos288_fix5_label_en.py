"""
AIPOS-288 FIX-5: board_config label_en contract test.

Verifies:
  a) Frontend renders workspace label with EN preference (label_en > label fallback)
  b) Project import (the remaining board writer of board_config; server-side init was retired in AIPOS-F105) writes label_en when provided
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
    AIPOS-288 FIX-5a: project-detail.html must use workspaceLabel() in its render places:
      - createProjectHeader (H1)
      - browser title (document.title)
    (AIPOS-F171: the portal header render point retired with project-map.md)
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
    
    # 2) AIPOS-F171 件③: 门户头(读手写 project-map.md 的 portal 段)已随旧项目地图下线, 不再是渲染点
    assert "renderPortalHeader" not in content, "AIPOS-F171: portal header (project-map.md) must stay retired"

    # 3) Check document.title uses workspaceLabel
    # Look for document.title assignment after workspace data is loaded
    title_pattern = r'document\.title\s*=\s*[^;]*workspaceLabel\s*\(\s*workspace\s*\)'
    if not re.search(title_pattern, content):
        raise AssertionError(
            "AIPOS-288 FIX-5a: document.title must use workspaceLabel(workspace)"
        )


def test_server_import_accepts_label_en():
    """
    AIPOS-288 FIX-5b → AIPOS-F171 件③(gap #155): the Board is read-only. The last board writer of board_config
    (project import, POST /api/project-structure/import, which used to write label_en into board_config.json) is offline:
    the route answers 405 BOARD_READ_ONLY and writes nothing; the import route implementation is gone from app.py.
    """
    import tempfile

    from web.board.app import _api_routes, dispatch_api_request

    content = APP_PY.read_text(encoding='utf-8')
    assert "_workspace_init_route" not in content, "AIPOS-F105: board workspace init route must stay retired"
    assert "_project_structure_import_route" not in content, "AIPOS-F171: board import writer must stay offline"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "5_tasks" / "queue" / "pending").mkdir(parents=True)
        status, data = dispatch_api_request(method="POST", path="/api/project-structure/import", routes=_api_routes(root))
        assert status == 405 and data["error"] == "BOARD_READ_ONLY", data
        assert not (root / ".lybra" / "board_config.json").exists(), "read-only board must not write board_config.json"


def test_frontend_wizard_sends_label_en():
    """
    AIPOS-288 FIX-5b (retargeted by AIPOS-F105): overview.html confirmImport must send label_en in payload
    (serverSideInit was retired with the board init entry).
    """
    if not OVERVIEW_HTML.exists():
        raise AssertionError("overview.html not found")

    content = OVERVIEW_HTML.read_text(encoding='utf-8')
    assert "serverSideInit" not in content, "AIPOS-F105: board server-side init must stay retired"

    match = re.search(
        r'async\s+function\s+confirmImport\s*\([^)]*\)\s*\{(.*?)(?=\n\s{4}previewImportBtn\.addEventListener)',
        content,
        re.DOTALL
    )
    if not match:
        raise AssertionError("AIPOS-288 FIX-5b: confirmImport function not found")

    func_body = match.group(1)

    if "projectNameEnInput" not in func_body:
        raise AssertionError(
            "AIPOS-288 FIX-5b: confirmImport must read projectNameEnInput"
        )

    if not re.search(r'payload\.label_en\s*=', func_body):
        raise AssertionError(
            "AIPOS-288 FIX-5b: confirmImport must include label_en in API payload"
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
    AIPOS-288 FIX-6 → AIPOS-F171 件③(gap #156): /api/overview no longer reads the static board_config.json list (and so
    no longer passes its label_en through): the project list is derived from the governance home root (one enumeration
    shared with the Owner inbox, owner_inbox.home_projects) and each workspace carries its project name as label plus the
    paused marker. The overview assembly must not read board_config / label_en any more.
    """
    content = APP_PY.read_text(encoding='utf-8')
    match = re.search(r'def\s+get_overview\s*\([^)]*\)\s*->.*?:\s*(.*?)(?=\ndef\s+)', content, re.DOTALL)
    assert match, "get_overview function not found"
    body = match.group(1)
    assert "_board_workspaces(" in body, "get_overview must take the home-root derived project list"
    assert "label_en" not in body and "board_config" not in body, "board_config label_en passthrough must stay retired"
    ws = re.search(r'def\s+_board_workspaces\s*\([^)]*\)\s*->.*?:\s*(.*?)(?=\ndef\s+)', content, re.DOTALL)
    assert ws and "home_projects(" in ws.group(1), "_board_workspaces must reuse owner_inbox.home_projects"


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
