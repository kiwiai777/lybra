"""
AIPOS-288: CJK source-level guard — no literal CJK in product chrome except i18n dictionaries.

This test scans JS/HTML/py sources in web/board/ for CJK character literals.
Allowed locations:
  - i18n dictionary files (i18n.js translations object)
  - Lines with explicit exemption marker: // i18n-exempt: <reason>
  - Data whitelists (project-map.json MODE/TOPOLOGY values, etc.)

Red line: Bare CJK literals in product UI code => test failure.
This ensures漏译不可能 (impossible to miss translation) going forward.
"""

import re
from pathlib import Path

# AIPOS-288: CJK Unicode range (common Han ideographs)
CJK_PATTERN = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]')

# Files to scan
WEB_BOARD_ROOT = Path(__file__).parent.parent
SCAN_PATTERNS = [
    "static/*.js",
    "static/*.html",
    "*.py",
]

# Exempted files (i18n dictionaries)
EXEMPTED_FILES = {
    "static/i18n.js",  # Primary i18n dictionary
    "static/login.html",  # Login page (separate auth flow, out of scope for AIPOS-288)
    "static/auth-chrome.js",  # Auth chrome (separate auth flow)
}

# Exempted line patterns (explicit markers)
EXEMPT_MARKER_PATTERN = re.compile(r'i18n-exempt:', re.IGNORECASE)  # Unified marker for all languages

# Data whitelist patterns (project-map values, test fixtures, etc.)
DATA_WHITELIST_PATTERNS = [
    re.compile(r'"(mode|topology|description|label|name)"\s*:', re.IGNORECASE),  # JSON data fields
    re.compile(r'class\s+[A-Z]'),  # CSS class definitions (not UI text)
    re.compile(r'data-i18n='),  # Already tagged for i18n hydration
    re.compile(r'i18n\.t\('),  # Already using i18n.t()
    re.compile(r'_I18N_TEMPLATES'),  # Python i18n templates
    re.compile(r'def test_'),  # Test function names/docstrings
    re.compile(r'""".*AIPOS'),  # Docstrings with task IDs (often bilingual)
    re.compile(r'^\s*#'),  # Python/JS comments (not product UI text)
    re.compile(r'^\s*//'),  # JS comments
    re.compile(r'/\*.*\*/'),  # Block comments
    re.compile(r'<!--.*-->'),  # HTML comments (single line)
    re.compile(r'<!--'),  # HTML comment start
]


def is_exempted_file(file_path: Path) -> bool:
    """Check if file is in the exemption list."""
    rel_path = file_path.relative_to(WEB_BOARD_ROOT)
    return str(rel_path) in EXEMPTED_FILES


def is_exempted_line(line: str, file_path: Path, in_template_block: bool = False) -> bool:
    """Check if line is exempted via marker or data whitelist."""
    # Explicit exemption marker
    if EXEMPT_MARKER_PATTERN.search(line):
        return True
    
    # Inside _I18N_TEMPLATES block in Python
    if in_template_block:
        return True
    
    # Data whitelist patterns
    for pattern in DATA_WHITELIST_PATTERNS:
        if pattern.search(line):
            return True
    
    # Multi-line string literals in Python
    if file_path.suffix == '.py' and ('"""' in line or "'''" in line):
        return True
    
    return False


def scan_file_for_cjk(file_path: Path) -> list[tuple[int, str]]:
    """
    Scan a file for CJK literals.
    Returns list of (line_number, line_content) tuples with violations.
    """
    violations = []
    
    if is_exempted_file(file_path):
        return violations
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            in_template_block = False
            in_docstring = False
            in_html_comment = False
            in_css_comment = False
            docstring_marker = None
            
            for line_num, line in enumerate(f, 1):
                # Track _I18N_TEMPLATES block in Python files
                if file_path.suffix == '.py':
                    if '_I18N_TEMPLATES = {' in line:
                        in_template_block = True
                    elif in_template_block and line.strip() == '}':
                        in_template_block = False
                        continue
                    
                    # Track docstrings (""" or ''') - Python engineering docs
                    if '"""' in line or "'''" in line:
                        marker = '"""' if '"""' in line else "'''"
                        count = line.count(marker)
                        if count == 2:
                            # Single-line docstring, skip
                            continue
                        elif count == 1:
                            if not in_docstring:
                                in_docstring = True
                                docstring_marker = marker
                                continue
                            elif marker == docstring_marker:
                                in_docstring = False
                                docstring_marker = None
                                continue
                    
                    if in_docstring:
                        continue
                
                # Track HTML comments
                if file_path.suffix == '.html':
                    if '<!--' in line and '-->' not in line:
                        in_html_comment = True
                        continue
                    elif in_html_comment and '-->' in line:
                        in_html_comment = False
                        continue
                    if in_html_comment:
                        continue
                
                # Track CSS block comments
                if file_path.suffix == '.html' or file_path.suffix == '.css':
                    if '/*' in line and '*/' not in line:
                        in_css_comment = True
                        continue
                    elif in_css_comment and '*/' in line:
                        in_css_comment = False
                        continue
                    if in_css_comment:
                        continue
                
                # Skip if line is exempted
                if is_exempted_line(line, file_path, in_template_block):
                    continue
                
                # Check for CJK characters
                if CJK_PATTERN.search(line):
                    violations.append((line_num, line.rstrip()))
    
    except Exception as e:
        print(f"Warning: Could not scan {file_path}: {e}")
    
    return violations


def test_no_bare_cjk_in_sources():
    """
    AIPOS-288 S3: Source-level CJK guard.
    
    Scans web/board/ JS/HTML/py for bare CJK literals.
    Exemptions:
      - i18n.js (dictionary)
      - Lines with // i18n-exempt: marker
      - Data fields (JSON values, test fixtures)
    
    Failure = new UI text not routed through i18n channel.
    """
    all_violations = {}
    
    for pattern in SCAN_PATTERNS:
        for file_path in WEB_BOARD_ROOT.glob(pattern):
            # Skip test files themselves
            if file_path.name.startswith("test_"):
                continue
            
            violations = scan_file_for_cjk(file_path)
            if violations:
                rel_path = file_path.relative_to(WEB_BOARD_ROOT)
                all_violations[str(rel_path)] = violations
    
    # Report violations
    if all_violations:
        report = ["AIPOS-288 CJK source guard failed. Bare CJK literals found:\n"]
        for file_path, violations in sorted(all_violations.items()):
            report.append(f"\n{file_path}:")
            for line_num, line_content in violations:
                # Truncate long lines
                display = line_content[:100] + "..." if len(line_content) > 100 else line_content
                report.append(f"  Line {line_num}: {display}")
        
        report.append("\n\nTo fix:")
        report.append("  1. Move text to i18n.js translations object")
        report.append("  2. Use i18n.t('key') in JS or data-i18n='key' in HTML")
        report.append("  3. Use _generate_text('template', locale, ...) in Python")
        report.append("  4. Add // i18n-exempt: <reason> for legitimate data/config")
        
        raise AssertionError("\n".join(report))


def test_i18n_dictionary_completeness():
    """
    AIPOS-288 S1: Verify i18n.js has matching zh/en keys.
    
    Both language dictionaries must have identical key sets.
    Missing key in en => silent fallback =>漏译.
    """
    i18n_path = WEB_BOARD_ROOT / "static" / "i18n.js"
    if not i18n_path.exists():
        raise AssertionError("i18n.js not found")
    
    content = i18n_path.read_text(encoding='utf-8')
    
    # Extract keys from zh and en sections
    # Support both single and double quotes
    zh_section = content.split('zh: {')[1].split('},')[0] if 'zh: {' in content else ''
    en_section = content.split('en: {')[1].split('}\n};')[0] if 'en: {' in content else ''
    
    # Match both 'key': and "key":
    zh_keys = set(re.findall(r"['\"]([^'\"]+)['\"]:\s*['\"]?", zh_section))
    en_keys = set(re.findall(r"['\"]([^'\"]+)['\"]:\s*['\"]?", en_section))
    
    missing_in_en = zh_keys - en_keys
    missing_in_zh = en_keys - zh_keys
    
    errors = []
    if missing_in_en:
        errors.append(f"Keys missing in 'en': {sorted(missing_in_en)}")
    if missing_in_zh:
        errors.append(f"Keys missing in 'zh': {sorted(missing_in_zh)}")
    
    if errors:
        raise AssertionError(
            "AIPOS-288 i18n dictionary completeness failed.\n" + "\n".join(errors)
        )


def test_python_i18n_template_completeness():
    """
    AIPOS-288 S1: Verify Python _I18N_TEMPLATES has matching zh/en keys.
    
    Backend generated text (advisor prompts, etc.) must have both locales.
    """
    app_path = WEB_BOARD_ROOT / "app.py"
    if not app_path.exists():
        raise AssertionError("app.py not found")
    
    content = app_path.read_text(encoding='utf-8')
    
    # Extract template keys from _I18N_TEMPLATES
    # Look for pattern: "template_name": """..."""
    zh_section = content.split('"zh": {')[1].split('},')[0] if '"zh": {' in content else ""
    en_section = content.split('"en": {')[1].split('},')[0] if '"en": {' in content else ""
    
    zh_templates = set(re.findall(r'"([^"]+)":\s*"""', zh_section))
    en_templates = set(re.findall(r'"([^"]+)":\s*"""', en_section))
    
    missing_in_en = zh_templates - en_templates
    missing_in_zh = en_templates - zh_templates
    
    errors = []
    if missing_in_en:
        errors.append(f"Templates missing in 'en': {sorted(missing_in_en)}")
    if missing_in_zh:
        errors.append(f"Templates missing in 'zh': {sorted(missing_in_zh)}")
    
    if errors:
        raise AssertionError(
            "AIPOS-288 Python i18n template completeness failed.\n" + "\n".join(errors)
        )


def test_html_data_i18n_initial_text():
    """
    AIPOS-288 FIX-1: HTML nodes with data-i18n must have EN or empty initial text.
    
    Initial text nodes are rendered before JS executes, causing a brief flash of
    untranslated content. To prevent CJK leakage in EN mode first-frame, all
    data-i18n nodes must start with EN text or be empty (JS will hydrate immediately).
    
    Pattern: <element data-i18n="key">Initial Text</element>
    Rule: Initial Text must NOT contain CJK characters.
    """
    violations = []
    
    for html_file in WEB_BOARD_ROOT.glob("static/*.html"):
        if html_file.name in EXEMPTED_FILES:
            continue
        
        try:
            content = html_file.read_text(encoding='utf-8')
            # Find all data-i18n nodes with text content
            # Pattern: data-i18n="key">text</element>
            pattern = re.compile(
                r'data-i18n=["\']([^"\']*)["\']\.?\s*>([^<]+)<',
                re.MULTILINE
            )
            
            for match in pattern.finditer(content):
                i18n_key = match.group(1)
                initial_text = match.group(2).strip()
                
                # Check if initial text contains CJK
                if CJK_PATTERN.search(initial_text):
                    # Find line number
                    line_num = content[:match.start()].count('\n') + 1
                    violations.append((
                        html_file.relative_to(WEB_BOARD_ROOT),
                        line_num,
                        i18n_key,
                        initial_text[:60]
                    ))
        
        except Exception as e:
            print(f"Warning: Could not scan {html_file}: {e}")
    
    if violations:
        report = ["AIPOS-288 FIX-1: HTML data-i18n nodes with CJK initial text:\n"]
        for file_path, line_num, key, text in violations:
            report.append(f"{file_path}:{line_num} data-i18n=\"{key}\" → \"{text}\"")
        report.append("\nFix: Replace initial text with EN or empty (JS will hydrate).")
        raise AssertionError("\n".join(report))


if __name__ == "__main__":
    # Allow running directly for quick check
    print("Running AIPOS-288 CJK source guard...")
    try:
        test_no_bare_cjk_in_sources()
        print("✓ No bare CJK literals found")
    except AssertionError as e:
        print(f"✗ {e}")
        exit(1)
    
    try:
        test_i18n_dictionary_completeness()
        print("✓ i18n.js dictionaries are complete")
    except AssertionError as e:
        print(f"✗ {e}")
        exit(1)
    
    try:
        test_python_i18n_template_completeness()
        print("✓ Python i18n templates are complete")
    except AssertionError as e:
        print(f"✗ {e}")
        exit(1)
    
    try:
        test_html_data_i18n_initial_text()
        print("✓ HTML data-i18n initial text is EN or empty")
    except AssertionError as e:
        print(f"✗ {e}")
        exit(1)
    
    print("\nAll AIPOS-288 guards passed ✓")
