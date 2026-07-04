# AIPOS-241 (Slice β / F-o3-14) — copilot macOS TLS/CA: certifi-when-importable SSL context

- **status: draft** — R direction-audit **PASS** folded (2 amendments: SSL_CERT_FILE-first
  precedence + pip bootstrap note); awaiting Owner approval.
- **authority: NONE** — no product code, no commit until Owner approves.
- **R rulings folded:** ① precedence = explicit `SSL_CERT_FILE`/`SSL_CERT_DIR` env > certifi >
  system default — helper returns None when env is set (§1); verify test ⑤ pins it (§3). ② docs
  carry the bare-venv pip bootstrap (`SSL_CERT_FILE="$(<python-with-certifi> -m certifi)" pip
  install certifi`, one-time; `--trusted-host` forbidden) (§2).
- **parent:** macOS Track-2 O3 finding **F-o3-14** (copilot HTTPS `CERTIFICATE_VERIFY_FAILED` on a
  bare macOS venv).
- **severity:** substantive (macOS UX blocker for copilot chat; gate unaffected).
- **★ RED LINE (Owner anchor): certificate verification is NEVER disabled or downgraded.** Any
  `ssl._create_unverified_context` / `CERT_NONE` / `check_hostname=False` shape is an auto-reject —
  in this DRAFT, in review, and as a pinned regression test.

## §0 Symptom + root cause

- **Symptom (Owner O3, macOS):** copilot chat → `URLError: CERTIFICATE_VERIFY_FAILED`. Even pip's
  handshake fails on the same interpreter (pip only survives elsewhere because it vendors its own
  certifi).
- **Mechanism (code-located):** the ONLY outbound LLM site is `tools/lybra_tui/copilot.py`
  `LLMClient.__init__:144` — `build_opener(ProxyHandler({}))` with **no HTTPS context** → urllib
  falls back to `ssl.create_default_context()` with the **platform default CA paths**. On Linux
  that's `/etc/ssl/certs` (works). On a bare macOS venv (python.org build), the default verify
  paths are **empty** unless the user ran `Install Certificates.command` / set `SSL_CERT_FILE` →
  every HTTPS handshake fails. This is an **environment property of macOS pythons, not a Lybra
  defect** — but the product can heal it safely when `certifi` happens to be installed.

## §1 Product change (one file: `tools/lybra_tui/copilot.py`)

Add a small module-level helper + one construction branch in `LLMClient.__init__`:

```python
def _ssl_context_for_llm() -> ssl.SSLContext | None:
    """CA source precedence (R ruling): explicit env > certifi > system default.

    SSL_CERT_FILE / SSL_CERT_DIR set -> return None: urllib's default context already honors
    them, and an explicit operator choice must never be overridden by certifi. Otherwise use
    certifi's bundle when importable (macOS bare venvs have empty default CA paths). None ->
    caller keeps urllib's default behavior, byte-identical to today. Verification is NEVER
    relaxed: create_default_context = CERT_REQUIRED + check_hostname=True."""
    if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
        return None  # explicit env wins; default context honors it
    try:
        import certifi  # optional; TUI-extra environments only
    except ImportError:
        return None
    return ssl.create_default_context(cafile=certifi.where())
```

`__init__`:
```python
context = _ssl_context_for_llm()
if context is not None:
    self._opener = _request.build_opener(_request.ProxyHandler({}), _request.HTTPSHandler(context=context))
else:
    self._opener = _request.build_opener(_request.ProxyHandler({}))   # today's line, byte-identical
```

Properties:
- **`ssl.create_default_context(cafile=…)` = full verification** (`CERT_REQUIRED`,
  `check_hostname=True`) — strictly the same trust *policy* as today, only the trust *store* source
  changes to certifi's bundle. No parameter that weakens verification exists in the diff.
- **No certifi → byte-identical**: the `else` branch is today's exact constructor; bare/zero-dep
  environments never see a behavior change. `certifi` is NOT added to any dependency list —
  purely opportunistic (TUI venvs get it transitively or by the runbook step).
- **Gate / zero-dep zero-awareness**: the change is inside `tools/lybra_tui/` (TUI extra domain);
  gate core imports nothing from it; the guarded `import certifi` lives inside the helper (module
  import stays clean on bare python). `ProxyHandler({})` (proxy bypass), `_USER_AGENT`, timeout,
  key-in-header-only — all unchanged.
- copilot **read-only / scopes `[]` / zero-write** untouched (no scope/credential/write-path code
  in the diff).

## §2 Docs (runbook + README + Track-2 exercise) — environment requirement, not a Lybra defect

- `docs/v1_release_macos_runbook.md` + `docs/v1_macos_track2_exercise.md` (Pre-flight): add a
  **macOS certificates step** for the TUI venv, framed as an environment requirement of macOS
  pythons: EITHER `<tui-venv>/bin/pip install certifi` (recommended; the product auto-uses it) OR
  `export SSL_CERT_FILE=$(python3 -c 'import certifi; print(certifi.where())')` OR (python.org
  installs) run `Install Certificates.command`. State explicitly: **never** work around a TLS error
  by disabling verification.
- **Bootstrap note (R):** on a truly bare venv, pip ITSELF cannot handshake (no CA), so the certifi
  install needs a one-time bootstrap CA from an interpreter that already has certifi (system
  python3 / conda): `SSL_CERT_FILE="$(<python-with-certifi> -m certifi)" <tui-venv>/bin/pip install
  certifi` — after which the venv is self-sufficient. **`--trusted-host` is FORBIDDEN** (that's a
  verification bypass, red-line class).
- **Precedence (R ruling), stated in the docs:** explicit `SSL_CERT_FILE`/`SSL_CERT_DIR` env >
  certifi > system default. An operator who sets the env keeps full control; certifi only fills the
  gap when nothing explicit is set.
- `README.md` (TUI/copilot prerequisites): one line — on macOS, install `certifi` into the TUI venv
  (or set `SSL_CERT_FILE`); Lybra picks certifi up automatically (explicit env wins) and never
  disables verification.

## §3 Verify — positive truth

New tests in `tools/lybra_tui/tests/test_copilot.py` (or sibling file), stdlib-only:
1. **certifi present (positive assert on the real construction path):** inject a stub `certifi`
   module (`sys.modules`) whose `where()` returns a known path; capture
   `ssl.create_default_context`'s `cafile` kwarg (patch at the copilot module's `ssl` reference,
   restore after); construct `LLMClient` → assert (a) `cafile == stub.where()`, (b) the opener
   carries an `HTTPSHandler` built with that context — the context actually reaches the opener,
   not just gets created.
2. **anti-downgrade pin (red line):** on the certifi path, assert the REAL returned context has
   `verify_mode == ssl.CERT_REQUIRED` and `check_hostname is True`. A future edit that relaxes
   either turns this red.
3. **certifi absent → byte-identical:** with `certifi` import blocked, assert
   `_ssl_context_for_llm() is None` and the opener construction matches today's shape (no
   context-bearing HTTPSHandler added). This is the bare-lane guarantee.
4. Existing copilot tests unchanged (zero-write / scopes / header assertions stay green).
5. **env-first (R ruling ⑤):** with `SSL_CERT_FILE` (and separately `SSL_CERT_DIR`) set in the
   environment AND a certifi stub importable → `_ssl_context_for_llm()` returns **None** (explicit
   env wins; certifi does not override the operator), and the opener takes today's default branch.

Lanes: BARE (no certifi → branch 3 exercised naturally) / SYSTEM (miniconda has certifi → branch 1
exercised naturally) / TUI 105 / ACCEPTANCE PASS — including the "gate runs with ALL third-party
blocked" probe (the guarded import must not break it).

## §4 O3 real-hardware acceptance (Owner, native Mac)

On the Mac: **no `SSL_CERT_FILE` set**, TUI venv has ONLY `pip install certifi` added → launch via
the Track-2 flow → copilot chat one sentence → **200 + conformant card** (closes the F-o3-14 loop
together with F-o3-1's 403→200 re-confirmation, same chat). Negative sanity (optional): a venv
without certifi still fails with the honest `CERTIFICATE_VERIFY_FAILED` — NOT silently unverified.

## §5 Red lines (R make-or-break)

- **No verification downgrade anywhere** (no `CERT_NONE` / `check_hostname=False` /
  `_create_unverified_context`); pinned by test 2.
- No new dependency declared (certifi is opportunistic); no-certifi path **byte-identical**.
- `git diff` = `copilot.py` + its test + 3 docs ONLY; gate/★A1/two-root/zero-dep-core untouched;
  copilot read-only / scopes `[]` / zero-write / proxy-bypass / User-Agent unchanged.
- Docs frame it as a macOS environment requirement (with the safe remedies), never as "turn off TLS".

## §6 R direction-audit — PASS, rulings folded

- Pattern blessed (certifi-when-importable; fallback = today's byte-identical line).
- Test 1's positive assertion shape confirmed (capture `cafile` + context-reaches-opener).
- Docs remedies + "never disable verification" phrasing confirmed; bootstrap note added (§2, R ②).
- **Precedence RULED: `SSL_CERT_FILE`/`SSL_CERT_DIR` env FIRST** — the helper returns None when
  either is set (urllib's default context honors them; certifi never overrides an explicit operator
  choice); certifi fills the gap only when no env is set (§1); pinned by verify test ⑤ (§3);
  precedence stated in the docs (§2).
