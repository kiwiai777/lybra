"""AIPOS-F250 — the credential judgement learns signing-tool password forms (apksigner `--ks-pass pass:<value>` and friends).

Field case (10-28, AIPOS-F247 executor on the real roots): audit reports of a customer project carried
`apksigner sign ... --ks-pass pass:<value> --key-pass pass:<value>`; the ingest credential judgement (AIPOS-F239,
transitions artifact_ingest.credential_guard) did not know the form, so the history & audit page showed the keystore password as is.

item 1  declared patterns (single declaration, single matcher artifact_ingest._credential_matches): apksigner / bundletool / openssl
        `pass:<value>` (env: / file: / stdin are references = no hit, only the value after pass: is masked), keytool / jarsigner
        `-storepass` / `-keypass` (the `:env` / `:file` flag forms = no hit), gradle injected signing properties and quoted DSL literals,
        macOS `security ... -p / -P`, keystore key-value (`KEYSTORE_PASS=`); placeholders (<...>, ***, ${VAR}, $VAR) never hit.
        One occurrence hit by several patterns counts once (the first declared, specific before generic).
item 2  every consumer goes through the same matcher (ingest refusal, gate-side refusal, stock scan CLI, history expanded content,
        history report view) and the history index carries the judgement version: a judgement change rebuilds old entries.

Rig = the AIPOS-F247 rig (temporary HOME + temporary home root built through product commands). Every "password" here is an
obviously invented value assembled at run time (never a real credential).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f171_roadmap_board_readonly import _cli  # noqa: E402
from test_aipos_f247_history_search import ADV_X, C, _closed, _page, _search_json, board, rig  # noqa: E402,F401  - F247 rig (fixtures)
from tools.aipos_cli import artifact_ingest as ai  # noqa: E402

PW = "Inv" + "3nted" + "Kq7"  # invented value (assembled at run time; no exempt substring)
PW2 = "Mz" + "8Rt" + "4Lw" + "q"
F250_KINDS = ("signing_pass_arg", "keystore_pass_flag", "gradle_signing_password", "keychain_pass_flag", "keystore_pass_keyvalue")
MASK = "[M]"


def _show(text: str) -> None:
    sys.__stdout__.write(text + "\n")
    sys.__stdout__.flush()


def _decl() -> dict:
    decl = ai.credential_guard_declaration()
    assert decl is not None
    return decl


# (line, expected kind, values that must disappear once masked)
POSITIVE = [
    (f"apksigner sign --ks debug.keystore --ks-key-alias dbgkey --ks-pass pass:{PW} --key-pass pass:{PW2} app.apk",
     "signing_pass_arg", (PW, PW2)),
    (f'apksigner sign --ks-pass "pass:{PW}" app.apk', "signing_pass_arg", (PW,)),
    (f"bundletool build-apks --ks=r.jks --ks-pass=pass:{PW} --key-pass=pass:{PW2}", "signing_pass_arg", (PW, PW2)),
    (f"openssl pkcs12 -export -in c.pem -passout pass:{PW}", "signing_pass_arg", (PW,)),
    (f"openssl rsa -in k.pem -passin pass:{PW}", "signing_pass_arg", (PW,)),
    (f"keytool -genkeypair -keystore d.keystore -storepass {PW} -keypass {PW2}", "keystore_pass_flag", (PW, PW2)),
    ("keytool -list -keystore d.keystore -storepass 482916", "keystore_pass_flag", ("482916",)),  # digits are a real password here
    (f"keytool -importkeystore -srcstorepass {PW} -deststorepass {PW2}", "keystore_pass_flag", (PW, PW2)),
    (f"jarsigner -keystore r.jks -storepass {PW} app.jar rel", "keystore_pass_flag", (PW,)),
    (f"./gradlew assembleRelease -Pandroid.injected.signing.store.password={PW} -Pandroid.injected.signing.key.password=482916",
     "gradle_signing_password", (PW, "482916")),
    (f'    storePassword "{PW}"', "gradle_signing_password", (PW,)),
    (f"    keyPassword '{PW2}'", "gradle_signing_password", (PW2,)),
    (f'    storePassword = "{PW}"', "gradle_signing_password", (PW,)),
    (f"security unlock-keychain -p {PW} build.keychain", "keychain_pass_flag", (PW,)),
    (f"security import dist.p12 -k build.keychain -P {PW} -T /usr/bin/codesign", "keychain_pass_flag", (PW,)),
    (f"KEYSTORE_PASS={PW}", "keystore_pass_keyvalue", (PW,)),
    (f"export ANDROID_KEY_PASS={PW}", "keystore_pass_keyvalue", (PW,)),
    (f"storePass: {PW}", "keystore_pass_keyvalue", (PW,)),
    # fastlane / xcodebuild environment-variable style was already the key-value pattern's (proved here, not re-declared)
    (f"MATCH_PASSWORD={PW} fastlane match appstore", "keyvalue_secret", (PW,)),
    (f"FASTLANE_PASSWORD={PW}", "keyvalue_secret", (PW,)),
]

NEGATIVE = [
    "apksigner sign --ks-pass env:KS_PASS --key-pass env:KEY_PASS app.apk",
    "apksigner sign --ks-pass file:runtime/private/ks-pass.txt app.apk",
    "apksigner sign --ks-pass stdin app.apk",
    "bundletool build-apks --ks-pass=env:KS_PASS --key-pass=file:/run/kp",
    "--ks-pass pass:<口令>", "--ks-pass pass:***", "--ks-pass pass:${KS_PASS}", "--ks-pass pass:$KS_PASS", "--ks-pass pass:[已打码]",
    "keytool -list -storepass:env KS_PASS -keypass:file /run/kp",
    "keytool -storepass <口令> -keypass [已打码]", "-storepass 与 -keypass 只按名引用", "`-storepass` / `-keypass` 参数",
    "keytool -storepasswd -new <新口令>",
    "-Pandroid.injected.signing.store.password=$STORE_PASSWORD", "-Pandroid.injected.signing.key.password=${KEY_PASSWORD}",
    "storePassword System.getenv('KS_PASS')", "storePassword keystoreProperties['storePassword']",
    'storePassword = keystoreProperties["storePassword"]',
    'security unlock-keychain -p "$KEYCHAIN_PASSWORD" build.keychain',
    "KEYSTORE_PASS=${KEYSTORE_PASS}", "KEY_PASS: <口令>", "keypass 字段只按名引用",
    "PASS: tests/test_x.py", "pass: 12 failed: 0",
]


# ===========================================================================
# item 1: each form hits (value masked, only the value), references / placeholders do not
# ===========================================================================

def test_item1_signing_forms_hit_and_only_the_value_is_masked():
    decl = _decl()
    kinds = [k for k, _l, _rx, _low, _loose in decl["_patterns"]]
    assert set(F250_KINDS) <= set(kinds)
    assert max(kinds.index(k) for k in F250_KINDS) < min(kinds.index("keyvalue_secret"), kinds.index("cli_secret_flag")), \
        "specific forms are declared before the generic key-value / cli-flag patterns"
    for line, kind, values in POSITIVE:
        hits = ai.credential_hits(line, decl)
        shown = ai.credential_redact(line, decl, MASK)
        _show(f"[item1 +] {[h['kind'] for h in hits]} {shown}")
        assert {h["kind"] for h in hits} == {kind}, (line, hits)
        assert all(v not in shown for v in values), shown
        assert all(set(h) == {"line", "kind", "label"} for h in hits), "no value field"
    # only the value after pass: is masked; the flag and the pass: prefix stay readable
    shown = ai.credential_redact(POSITIVE[0][0], decl, MASK)
    assert f"--ks-pass pass:{MASK} --key-pass pass:{MASK} app.apk" in shown


def test_item1_references_placeholders_and_prose_do_not_hit():
    decl = _decl()
    for line in NEGATIVE:
        hits = ai.credential_hits(line, decl)
        _show(f"[item1 -] {[h['kind'] for h in hits]} {line}")
        assert hits == [], (line, hits)
        assert ai.credential_redact(line, decl, MASK) == line


def test_item1_one_occurrence_counts_once_first_declared_wins():
    """`--ks-pass=pass:<v>` is also a key-value shape and `-Pandroid...password=<v>` also the generic key-value pattern: one place =
    one hit (the specific kind); the redaction still masks every hit span."""
    decl = _decl()
    for line, kind in ((f"bundletool --ks-pass=pass:{PW}", "signing_pass_arg"),
                       (f"-Pandroid.injected.signing.store.password={PW}", "gradle_signing_password"),
                       (f"--pass pass:{PW}", "signing_pass_arg")):
        hits = ai.credential_hits(line, decl)
        assert [h["kind"] for h in hits] == [kind], (line, hits)
        assert PW not in ai.credential_redact(line, decl, MASK)
    # two occurrences on one line of the same kind: still one row per (line, kind), as before
    assert len(ai.credential_hits(f"--ks-pass pass:{PW} --key-pass pass:{PW2}", decl)) == 1


def test_item1_fingerprint_is_the_judgement_version():
    decl = _decl()
    fp = ai.credential_guard_fingerprint()
    assert fp == ai.credential_guard_fingerprint(decl) and len(fp) == 16
    older = {**decl, "patterns": [p for p in decl["patterns"] if p["kind"] not in F250_KINDS]}
    assert ai.credential_guard_fingerprint(older) != fp
    assert ai.credential_guard_fingerprint({**decl, "texts": {}}) == fp, "wording is not the judgement"


# ===========================================================================
# item 2: every consumer goes through the same matcher; history index rebuilt when the judgement changes
# ===========================================================================

APK_CMD = (f"apksigner sign --ks /w/debug.keystore --ks-key-alias dbgkey --ks-pass pass:{PW} --key-pass pass:{PW2} "
           "app/build/outputs/apk/release/app-release-unsigned.apk")


def _signed_card(gov: Path, task_id: str = "X-40") -> None:
    _closed(gov, C, task_id, title=f"{task_id} APK 签名核验", owner=ADV_X, goal="签名包核验", summary="签名包核验通过",
            audit="独立重跑签名通过", evidence=APK_CMD, days=0.5)


def test_item2_consumers_share_the_matcher(board, monkeypatch):
    from tools.aipos_cli.next_resolver import verdict_artifact_dir

    gov = board["gc"]
    _signed_card(gov)
    calls: list[str] = []
    real = ai._credential_matches

    def spy(text, decl):
        calls.append(text)
        return real(text, decl)

    monkeypatch.setattr(ai, "_credential_matches", spy)
    seen: dict[str, int] = {}

    def mark(name: str) -> None:
        seen[name] = len(calls)
        calls.clear()

    # gate-side return / verdict write entry (AIPOS-F242) and the ingest refusal it shares (AIPOS-F239)
    reasons = ai.gate_credential_blocking(gov, "X-40R", kind="verdict", card_fm={"task_id": "X-40R"},
                                          report=verdict_artifact_dir(gov, "X-40R") / "RETURN.md")
    mark("gate_credential_blocking(verdict)")
    _show(f"[item2 gate verdict] {reasons}")
    assert reasons and reasons[0].startswith("INGEST_CREDENTIAL_IN_ARTIFACT:") and "签名 / 加密工具口令参数" in reasons[0]
    assert PW not in "".join(reasons) and PW2 not in "".join(reasons)
    refusal = ai.credential_refusal(gov, verdict_artifact_dir(gov, "X-40R"), kind="verdict",
                                    report=verdict_artifact_dir(gov, "X-40R") / "RETURN.md")
    mark("credential_refusal(ingest)")
    assert refusal and {h["kind"] for h in refusal["credential_hits"]} == {"signing_pass_arg"}
    # stock read-only check CLI
    rc, out, err = _cli("--workspace-root", str(gov), "artifact", "credential-scan", "--json")
    mark("lybra artifact credential-scan")
    data = json.loads(out)
    _show(f"[item2 stock scan] rc={rc} by_kind={data['by_kind']}")
    assert rc == 1 and data["by_kind"].get("signing_pass_arg") and PW not in out and PW2 not in out
    # history expanded content + report view
    rc, res = _search_json(board, "签名核验")
    mark("lybra history search")
    cmd = res["results"][0]["verification"]["entries"][0]["command"]
    _show(f"[item2 history command] {cmd}")
    assert PW not in cmd and PW2 not in cmd and "--ks-pass pass:[已打码] --key-pass pass:[已打码]" in cmd
    from tools.aipos_cli.history_search import report_doc

    doc = report_doc(C, gov, "X-40", "audit")
    mark("history_search.report_doc")
    assert doc["problem"] is None and PW not in doc["body"] and "--ks-pass pass:[已打码]" in doc["body"]
    _show(f"[item2 consumers through artifact_ingest._credential_matches] {seen}")
    assert all(n > 0 for n in seen.values()), seen


def test_item2_history_page_and_report_view_masked(board):
    """HBLX-110V shape on the page: the expanded hit and the audit report view both show the masked command."""
    _signed_card(board["gc"])
    page = _page(board, f"/owner/history?project={C}&q={quote('签名核验')}")
    doc = _page(board, f"/owner/history?project={C}&task=X-40&doc=audit")
    excerpt = doc[doc.index('data-doc="audit"'):][:1200]
    _show(f"[item2 page audit report view] {excerpt}")
    for text in (page, doc):
        assert PW not in text and PW2 not in text and "pass:[已打码]" in text


def test_item2_index_rebuilt_when_the_judgement_changes(rig, monkeypatch):
    """An index written under the old judgement (no signing forms) holds the value; once the judgement changes the header differs
    (judgement version in the declaration digest) and the whole index is rebuilt with the masked text."""
    from tools.aipos_cli import frontmatter, history_search
    from tools.aipos_cli import next_resolver as nr
    from tools.aipos_cli.history_search import index_path, search

    monkeypatch.setattr(frontmatter, "_FILE_CACHE_RACY_WINDOW_NS", 0)  # let the fresh fixture files be kept in the index
    _signed_card(rig["gc"])
    current = nr._artifact_ingest_declaration

    def old_judgement():
        decl = json.loads(json.dumps(current()))
        guard = decl["credential_guard"]
        guard["patterns"] = [p for p in guard["patterns"] if p["kind"] not in F250_KINDS]
        return decl

    monkeypatch.setattr(nr, "_artifact_ingest_declaration", old_judgement)
    before = search(rig["home"], ["签名核验"])
    path = index_path(C, rig["gc"])
    leaked = PW in path.read_text(encoding="utf-8")
    _show(f"[item2 old judgement] value in index: {leaked}; index={[i for i in before['index'] if i['project'] == C]}")
    assert leaked, "precondition: the old judgement did not know the form (the field case)"
    monkeypatch.setattr(nr, "_artifact_ingest_declaration", current)
    history_search._LOADED.clear()
    after = search(rig["home"], ["签名核验"])
    idx = next(i for i in after["index"] if i["project"] == C)
    _show(f"[item2 new judgement] index={idx}")
    assert idx["rebuilt_reason"] == "header changed" and idx["rebuilt"] == idx["cards"] and idx["reused"] == 0
    assert PW not in path.read_text(encoding="utf-8") and PW2 not in path.read_text(encoding="utf-8")
    cmd = after["results"][0]["verification"]["entries"][0]["command"]
    assert PW not in cmd and "pass:[已打码]" in cmd
    again = search(rig["home"], ["签名核验"])
    assert next(i for i in again["index"] if i["project"] == C)["reused"] > 0, "same judgement = incremental again"


@pytest.mark.parametrize("value", [PW])
def test_item2_value_not_searchable(board, value):
    _signed_card(board["gc"])
    rc, res = _search_json(board, value)
    assert rc == 1 and res["total"] == 0
