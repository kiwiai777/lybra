"""
AIPOS-288 FIX-4: data-i18n applier mechanism contract test.

Verifies that i18n.js implements the applyTranslations() function with:
  a) querySelectorAll('[data-i18n]') + textContent assignment
  b) querySelectorAll('[data-i18n-attr]') + setAttribute for attributes
  c) Export in window.i18n API
  d) DOMContentLoaded trigger + switchLanguage integration

This test ensures the applier mechanism exists and has the required contract,
preventing silent i18n failures where HTML declares data-i18n but JS never
applies translations.
"""

import re
from pathlib import Path


WEB_BOARD_ROOT = Path(__file__).parent.parent
I18N_JS_PATH = WEB_BOARD_ROOT / "static" / "i18n.js"


def test_applier_function_exists():
    """
    AIPOS-288 FIX-4a: i18n.js must contain applyTranslations() function.
    
    Function signature: applyTranslations(root = document)
    Must scan for [data-i18n] and [data-i18n-attr] attributes.
    """
    if not I18N_JS_PATH.exists():
        raise AssertionError("i18n.js not found")
    
    content = I18N_JS_PATH.read_text(encoding='utf-8')
    
    # Check for function definition
    if not re.search(r'function\s+applyTranslations\s*\(', content):
        raise AssertionError(
            "AIPOS-288 FIX-4a: applyTranslations() function not found in i18n.js"
        )
    
    # Check for data-i18n scanning
    if "querySelectorAll('[data-i18n]')" not in content:
        raise AssertionError(
            "AIPOS-288 FIX-4a: applyTranslations() must scan for [data-i18n] elements"
        )
    
    # Check for textContent assignment
    if not re.search(r'\.textContent\s*=\s*t\(', content):
        raise AssertionError(
            "AIPOS-288 FIX-4a: applyTranslations() must assign textContent using t(key)"
        )
    
    # Check for data-i18n-attr scanning
    if "querySelectorAll('[data-i18n-attr]')" not in content:
        raise AssertionError(
            "AIPOS-288 FIX-4a: applyTranslations() must scan for [data-i18n-attr] elements"
        )
    
    # Check for setAttribute usage
    if not re.search(r'\.setAttribute\s*\(', content):
        raise AssertionError(
            "AIPOS-288 FIX-4a: applyTranslations() must use setAttribute for attribute translation"
        )


def test_applier_trigger_on_domcontentloaded():
    """
    AIPOS-288 FIX-4b: applyTranslations() must be triggered on DOMContentLoaded.
    
    Initial page load must apply translations before user sees content.
    """
    if not I18N_JS_PATH.exists():
        raise AssertionError("i18n.js not found")
    
    content = I18N_JS_PATH.read_text(encoding='utf-8')
    
    # Check for DOMContentLoaded listener
    if "DOMContentLoaded" not in content:
        raise AssertionError(
            "AIPOS-288 FIX-4b: Must have DOMContentLoaded listener"
        )
    
    # Check that applyTranslations is called in the listener
    # Pattern: addEventListener('DOMContentLoaded', ... applyTranslations() ...)
    domready_section = re.search(
        r"addEventListener\s*\(\s*['\"]DOMContentLoaded['\"].*?\}\s*\)",
        content,
        re.DOTALL
    )
    
    if not domready_section or "applyTranslations" not in domready_section.group(0):
        raise AssertionError(
            "AIPOS-288 FIX-4b: DOMContentLoaded listener must call applyTranslations()"
        )


def test_applier_trigger_on_language_switch():
    """
    AIPOS-288 FIX-4b: applyTranslations() must be called in switchLanguage().
    
    When user switches language, all data-i18n elements must be re-translated.
    """
    if not I18N_JS_PATH.exists():
        raise AssertionError("i18n.js not found")
    
    content = I18N_JS_PATH.read_text(encoding='utf-8')
    
    # Extract switchLanguage function
    switch_func = re.search(
        r'function\s+switchLanguage\s*\([^)]*\)\s*\{.*?\n\}',
        content,
        re.DOTALL
    )
    
    if not switch_func:
        raise AssertionError("switchLanguage() function not found")
    
    func_body = switch_func.group(0)
    
    # Check that applyTranslations is called
    if "applyTranslations" not in func_body:
        raise AssertionError(
            "AIPOS-288 FIX-4b: switchLanguage() must call applyTranslations()"
        )


def test_applier_exported_in_window_api():
    """
    AIPOS-288 FIX-4b: applyTranslations must be exported in window.i18n API.
    
    Dynamic content rendering needs to call applyTranslations(container) after
    inserting data-i18n nodes.
    """
    if not I18N_JS_PATH.exists():
        raise AssertionError("i18n.js not found")
    
    content = I18N_JS_PATH.read_text(encoding='utf-8')
    
    # Check window.i18n export
    window_export = re.search(
        r'window\.i18n\s*=\s*\{[^}]+\}',
        content,
        re.DOTALL
    )
    
    if not window_export:
        raise AssertionError("window.i18n export not found")
    
    export_obj = window_export.group(0)
    
    if "applyTranslations" not in export_obj:
        raise AssertionError(
            "AIPOS-288 FIX-4b: applyTranslations must be exported in window.i18n"
        )


def test_all_data_i18n_keys_exist_in_dictionaries():
    """
    AIPOS-288 FIX-4c: All data-i18n keys referenced in HTML must exist in both zh/en.
    
    Prevents runtime silent failures where data-i18n="missing.key" falls back to key string.
    """
    # Extract all keys from i18n.js
    if not I18N_JS_PATH.exists():
        raise AssertionError("i18n.js not found")
    
    content = I18N_JS_PATH.read_text(encoding='utf-8')
    
    zh_section = content.split('zh: {')[1].split('},')[0] if 'zh: {' in content else ''
    en_section = content.split('en: {')[1].split('}\n};')[0] if 'en: {' in content else ''
    
    zh_keys = set(re.findall(r"['\"]([^'\"]+)['\"]:\s*['\"]?", zh_section))
    en_keys = set(re.findall(r"['\"]([^'\"]+)['\"]:\s*['\"]?", en_section))
    
    # Find all data-i18n references in HTML files
    html_keys = set()
    for html_file in WEB_BOARD_ROOT.glob("static/*.html"):
        try:
            html_content = html_file.read_text(encoding='utf-8')
            
            # Match data-i18n="key"
            matches = re.findall(r'data-i18n=["\']([^"\']+)["\']', html_content)
            html_keys.update(matches)
            
            # Match data-i18n-attr="attrName:key" or "attr:key;attr2:key2"
            attr_matches = re.findall(r'data-i18n-attr=["\']([^"\']+)["\']', html_content)
            for attr_spec in attr_matches:
                # Split by semicolon for multiple attributes
                for spec in attr_spec.split(';'):
                    if ':' in spec:
                        key = spec.split(':')[1].strip()
                        html_keys.add(key)
        
        except Exception as e:
            print(f"Warning: Could not scan {html_file}: {e}")
    
    # Check that all HTML keys exist in both dictionaries
    missing_in_zh = html_keys - zh_keys
    missing_in_en = html_keys - en_keys
    
    errors = []
    if missing_in_zh:
        errors.append(f"data-i18n keys missing in 'zh': {sorted(missing_in_zh)}")
    if missing_in_en:
        errors.append(f"data-i18n keys missing in 'en': {sorted(missing_in_en)}")
    
    if errors:
        raise AssertionError(
            "AIPOS-288 FIX-4c: HTML data-i18n keys not in dictionaries.\n" + "\n".join(errors)
        )


if __name__ == "__main__":
    print("Running AIPOS-288 FIX-4 applier contract tests...")
    
    tests = [
        ("Applier function exists", test_applier_function_exists),
        ("Applier triggered on DOMContentLoaded", test_applier_trigger_on_domcontentloaded),
        ("Applier triggered on language switch", test_applier_trigger_on_language_switch),
        ("Applier exported in window.i18n", test_applier_exported_in_window_api),
        ("All data-i18n keys in dictionaries", test_all_data_i18n_keys_exist_in_dictionaries),
    ]
    
    failed = []
    for name, test_func in tests:
        try:
            test_func()
            print(f"✓ {name}")
        except AssertionError as e:
            print(f"✗ {name}")
            print(f"  {e}")
            failed.append(name)
    
    if failed:
        print(f"\n{len(failed)} test(s) failed")
        exit(1)
    else:
        print("\nAll AIPOS-288 FIX-4 applier tests passed ✓")
