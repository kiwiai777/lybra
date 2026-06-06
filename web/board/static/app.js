const ROUTES = [
  ["health", "/api/health"],
  ["runtime-status", "/api/runtime-status"],
  ["lifecycle", "/api/lifecycle"],
  ["queue", "/api/queue"],
  ["needs-owner", "/api/needs-owner"],
  ["validate", "/api/validate"],
  ["agents", "/api/agents"],
  ["governance", "/api/governance"],
  ["drafts", "/api/drafts"],
  ["external-intake-review", "/api/external-intake/review"],
  ["planner-drafts-review", "/api/planner-drafts/review"],
  ["owner-decisions-review", "/api/owner-decisions/review"],
  ["owner-decision-records", "/api/owner-decision-records"],
  ["records", "/api/records"],
];
const ROUTE_BY_ID = Object.fromEntries(ROUTES);
const ACTOR_STORAGE_KEY = "aipos.board.previewActor";

let selectedTask = null;
let selectedNeedsOwner = null;
let selectedValidation = null;
let selectedRecord = null;
let selectedAgent = null;
let latestDebug = {};
let latestExecuteDryRun = null;
let latestDraftDryRun = null;
let latestDraftPublishDryRun = null;
let latestApprovedPlannerDraftDryRun = null;
let latestPlannerDraftReview = null;
let latestOrchestrationSummary = null;
let latestOrchestrationTimeline = null;
let latestOrchestrationIndex = null;
let latestPlannerLoopPreview = null;
let latestContextPackPreview = null;
let latestManualPlannerTickPreview = null;
let latestPlannerLoopPersistenceDryRun = null;
let latestPlannerLoopPersistenceResult = null;
let selectedOwnerDecision = null;
let selectedExternalIntake = null;
let selectedOwnerDecisionRecord = null;
let latestOwnerDecisionResolutionReview = null;
let latestOwnerDecisionRecordDryRun = null;
let latestAiAuthorPreview = null;
let latestProfilePreview = null;
let selectedLifecycle = null;

function writePanel(id, payload) {
  document.getElementById(id).textContent = JSON.stringify(payload, null, 2);
}

function setPanelLoading(id, label = "Loading...") {
  document.getElementById(id).textContent = label;
}

function setDetailPanelState(id, label) {
  const detailsByRoute = {
    records: ["records-list", "records-detail"],
    agents: ["agents-list", "agents-detail"],
  };
  const detail = detailsByRoute[id];
  if (!detail) {
    return;
  }
  const [listId, detailId] = detail;
  if (id === "records") {
    selectedRecord = null;
  }
  if (id === "agents") {
    selectedAgent = null;
  }
  document.getElementById(listId).replaceChildren();
  document.getElementById(detailId).textContent = label;
}

function summarizeEnvelope(data) {
  const category = Array.isArray(data?.errors) && data.errors[0] ? data.errors[0].category : undefined;
  const message = Array.isArray(data?.errors) && data.errors[0] ? data.errors[0].message : undefined;
  return {
    ok: data?.ok,
    verdict: data?.verdict,
    category,
    message,
    summary: data?.summary,
    warnings: data?.warnings || [],
    blocking_reasons: data?.blocking_reasons || [],
    needs_owner_reasons: data?.needs_owner_reasons || [],
  };
}

function setPanelError(id, error, data = null) {
  const payload = data ? summarizeEnvelope(data) : { ok: false, message: String(error) };
  writePanel(id, payload);
}

async function loadRoute(id, path) {
  setPanelLoading(id);
  setDetailPanelState(id, "Loading...");
  try {
    const response = await fetch(path, { cache: "no-store" });
    const data = await response.json();
    writePanel(id, summarizeEnvelope(data));
    if (data.ok === false) {
      setPanelError(id, null, data);
    }
    if (id === "queue") {
      renderTaskList(data);
    }
    if (id === "runtime-status") {
      renderRuntimeStatus(data);
    }
    if (id === "lifecycle") {
      renderLifecycle(data);
    }
    if (id === "needs-owner") {
      renderNeedsOwnerDetails(data);
    }
    if (id === "validate") {
      renderValidationDetails(data);
    }
    if (id === "records") {
      renderRecordsDetails(data);
    }
    if (id === "agents") {
      renderAgentsDetails(data);
    }
    if (id === "governance") {
      renderGovernancePanel(data);
    }
    if (id === "planner-drafts-review") {
      renderPlannerDraftReviewDesk(data);
    }
    if (id === "external-intake-review") {
      renderExternalIntakeReview(data);
    }
    if (id === "owner-decisions-review") {
      renderOwnerDecisionGate(data);
    }
    if (id === "owner-decision-records") {
      renderOwnerDecisionRecords(data);
    }
    return data;
  } catch (err) {
    setPanelError(id, err);
    setDetailPanelState(id, `Unable to load ${id}: ${String(err)}`);
    return { ok: false, error: String(err) };
  }
}

function taskRows(queueData) {
  const tasks = queueData?.data?.tasks;
  return Array.isArray(tasks) ? tasks : [];
}

function lifecycleRows(data) {
  const rows = data?.data?.tasks;
  return Array.isArray(rows) ? rows : [];
}

function envelopeRows(data) {
  const tasks = data?.data?.tasks;
  return Array.isArray(tasks) ? tasks : [];
}

function recordRows(data) {
  const sessions = Array.isArray(data?.data?.sessions) ? data.data.sessions.map((item) => ({ ...item, record_kind: "session" })) : [];
  const claims = Array.isArray(data?.data?.claims) ? data.data.claims.map((item) => ({ ...item, record_kind: "claim" })) : [];
  const ownerDecisions = Array.isArray(data?.data?.owner_decisions)
    ? data.data.owner_decisions.map((item) => ({ ...item, record_kind: "owner_decision" }))
    : [];
  return [...ownerDecisions, ...sessions, ...claims];
}

function externalIntakeRows(data) {
  return Array.isArray(data?.data?.drafts) ? data.data.drafts : [];
}

function ownerDecisionRecordRows(data) {
  return Array.isArray(data?.data?.records) ? data.data.records : [];
}

function aiAuthorValue(id) {
  return document.getElementById(id).value.trim();
}

function setupStatusLabel(present) {
  return present ? "present" : "missing";
}

function renderRuntimeStatus(data) {
  const card = document.getElementById("runtime-status-card");
  if (!card) {
    return;
  }
  const payload = data?.data || {};
  const workspace = payload.workspace || {};
  const endpoints = payload.endpoints || {};
  const setup = payload.agent_setup || {};
  const loop = payload.loop || {};
  const visibility = setup.tool_visibility || {};
  const stages = loop.counts || {};
  const warnings = Array.isArray(data?.warnings) ? data.warnings : [];
  const rows = [
    ["Workspace", workspace.root || "-"],
    ["Config", workspace.config_path || "not found"],
    ["Board", endpoints.board?.url || "-"],
    ["MCP", endpoints.mcp?.url || "-"],
    ["SSE", endpoints.mcp?.sse_url || "-"],
    ["Transport auth", `${setupStatusLabel(setup.transport_token_present)} (${setup.transport_token_env || "LYBRA_MCP_TOKEN"})`],
    ["Capability scope", `${setupStatusLabel(setup.capability_token_present)} (${setup.capability_token_env || "LYBRA_CAPABILITY_TOKEN"})`],
  ];
  card.replaceChildren();
  const grid = document.createElement("div");
  grid.className = "runtime-grid";
  for (const [label, value] of rows) {
    const item = document.createElement("div");
    item.className = "runtime-chip";
    const strong = document.createElement("strong");
    strong.textContent = label;
    const span = document.createElement("span");
    span.textContent = String(value);
    item.append(strong, span);
    grid.appendChild(item);
  }
  card.appendChild(grid);

  const setupBlock = document.createElement("div");
  setupBlock.className = "runtime-setup";
  const command = document.createElement("code");
  command.textContent = setup.server_command || "lybra mcp";
  setupBlock.appendChild(command);
  const auth = document.createElement("code");
  auth.textContent = setup.authorization_header_ref || "Bearer ${LYBRA_MCP_TOKEN}";
  setupBlock.appendChild(auth);
  card.appendChild(setupBlock);

  const toolList = document.createElement("div");
  toolList.className = "runtime-tools";
  for (const key of ["queue_claim", "queue_return", "audit_dispatch", "audit_verdict"]) {
    const pill = document.createElement("span");
    pill.className = `runtime-pill ${visibility[key] === "visible" ? "is-visible" : "is-hidden"}`;
    pill.textContent = `${key}: ${visibility[key] || "hidden"}`;
    toolList.appendChild(pill);
  }
  card.appendChild(toolList);

  const stageList = document.createElement("div");
  stageList.className = "runtime-tools";
  const stageKeys = Object.keys(stages).sort();
  if (stageKeys.length === 0) {
    const empty = document.createElement("span");
    empty.className = "runtime-pill";
    empty.textContent = "queue: empty";
    stageList.appendChild(empty);
  } else {
    for (const key of stageKeys) {
      const pill = document.createElement("span");
      pill.className = "runtime-pill";
      pill.textContent = `${key}: ${stages[key]}`;
      stageList.appendChild(pill);
    }
  }
  card.appendChild(stageList);

  const notice = document.createElement("p");
  notice.className = "runtime-notice";
  notice.textContent = setup.secrets_notice || "Raw tokens are never shown.";
  card.appendChild(notice);
  if (warnings.length > 0) {
    const warningList = document.createElement("ul");
    warningList.className = "runtime-warnings";
    for (const warning of warnings.slice(0, 4)) {
      const item = document.createElement("li");
      item.textContent = String(warning);
      warningList.appendChild(item);
    }
    card.appendChild(warningList);
  }
}

function lifecycleLabel(row) {
  return [row?.task_id, row?.lifecycle_stage, row?.owner_gate?.state, row?.provenance_completeness].filter(Boolean).join(" | ");
}

function renderCountPills(container, counts, prefix = "") {
  const keys = Object.keys(counts || {}).sort();
  if (keys.length === 0) {
    const pill = document.createElement("span");
    pill.className = "runtime-pill";
    pill.textContent = `${prefix}empty`;
    container.appendChild(pill);
    return;
  }
  for (const key of keys) {
    const pill = document.createElement("span");
    pill.className = "runtime-pill";
    pill.textContent = `${prefix}${key}: ${counts[key]}`;
    container.appendChild(pill);
  }
}

function renderLifecycle(data) {
  const summary = document.getElementById("lifecycle-summary");
  const list = document.getElementById("lifecycle-list");
  const detail = document.getElementById("lifecycle-detail");
  const rows = lifecycleRows(data);
  selectedLifecycle = null;
  summary.replaceChildren();
  list.replaceChildren();
  detail.textContent = rows.length === 0 ? "No lifecycle rows." : "Select a lifecycle row.";
  const stages = document.createElement("div");
  stages.className = "runtime-tools";
  renderCountPills(stages, data?.data?.stage_counts || {}, "stage ");
  summary.appendChild(stages);
  const gates = document.createElement("div");
  gates.className = "runtime-tools";
  renderCountPills(gates, data?.data?.owner_gate_counts || {}, "gate ");
  summary.appendChild(gates);
  const completeness = document.createElement("div");
  completeness.className = "runtime-tools";
  renderCountPills(completeness, data?.data?.provenance_completeness_counts || {}, "provenance ");
  summary.appendChild(completeness);
  for (const row of rows) {
    const button = document.createElement("button");
    button.className = "task-button";
    button.textContent = lifecycleLabel(row);
    button.title = row?.path || row?.task_id || "";
    button.addEventListener("click", () => selectLifecycle(row, button));
    list.appendChild(button);
  }
}

function selectLifecycle(row, sourceButton) {
  selectedLifecycle = row;
  clearSelected(document.getElementById("lifecycle-list"));
  sourceButton?.classList?.add("selected");
  renderLifecycleDetail(row);
}

function renderLifecycleDetail(row) {
  const detail = document.getElementById("lifecycle-detail");
  detail.replaceChildren();
  const header = document.createElement("div");
  header.className = "lifecycle-header";
  const title = document.createElement("strong");
  title.textContent = row?.task_id || row?.path || "Lifecycle row";
  const stage = document.createElement("span");
  stage.textContent = row?.lifecycle_stage || "unknown";
  header.append(title, stage);
  detail.appendChild(header);

  const gate = document.createElement("div");
  gate.className = `owner-gate-banner ${row?.owner_gate?.state && row.owner_gate.state !== "none" ? "warn" : ""}`;
  gate.textContent = row?.owner_gate?.label || "No Owner gate surfaced";
  detail.appendChild(gate);

  const refs = document.createElement("div");
  refs.className = "lifecycle-ref-grid";
  const refRows = row?.record_refs || {};
  for (const key of ["claim_id", "claim_record_ref", "active_session_id", "session_record_ref", "return_record_ref", "return_record_path", "audit_dispatch_record_ref", "related_audit_task_ref", "related_audit_verdict_ref"]) {
    const item = document.createElement("div");
    item.className = "runtime-chip";
    const label = document.createElement("strong");
    label.textContent = key;
    const value = document.createElement("span");
    value.textContent = refRows[key] || "-";
    item.append(label, value);
    refs.appendChild(item);
  }
  detail.appendChild(refs);

  const diagnostics = {
    provenance_completeness: row?.provenance_completeness,
    validator_verdict: row?.validator_verdict,
    recovery_verdict: row?.recovery_verdict,
    staleness: row?.staleness || [],
    contradictions: row?.contradictions || [],
    audit_relation: row?.audit_relation || {},
    recommended_next_action: row?.recommended_next_action,
    writes_enabled: row?.writes_enabled,
    execute_allowed: row?.execute_allowed,
  };
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(diagnostics, null, 2);
  detail.appendChild(pre);
}

function aiAuthorMode() {
  return document.querySelector('input[name="ai-author-mode"]:checked')?.value || "fixture";
}

function updateAiAuthorModeFields() {
  const live = aiAuthorMode() === "live";
  document.getElementById("ai-author-live-fields").hidden = !live;
  document.getElementById("ai-author-fixture").hidden = live;
}

function aiAuthorIntent() {
  const intentId = aiAuthorValue("ai-author-intent-id") || `board-intent-${Date.now()}`;
  return {
    intent_id: intentId,
    submitted_at: new Date().toISOString(),
    submitted_by: aiAuthorValue("ai-author-actor"),
    requirement: aiAuthorValue("ai-author-requirement"),
    project_hint: aiAuthorValue("ai-author-project-hint"),
    task_mode_hint: aiAuthorValue("ai-author-task-mode-hint"),
    task_class_hint: aiAuthorValue("ai-author-task-class-hint"),
    priority_hint: aiAuthorValue("ai-author-priority-hint"),
    output_target_hint: aiAuthorValue("ai-author-output-target-hint"),
    context_bundle_hint: aiAuthorValue("ai-author-context-bundle-hint"),
    retry_of: aiAuthorValue("ai-author-retry-of"),
  };
}

function summarizeAiAuthor(data) {
  const attempt = data?.data?.attempt || {};
  const intent = data?.data?.original_payload?.intent || {};
  const proposal = data?.data?.proposal || {};
  const frontmatter = proposal.frontmatter || {};
  const triage = data?.data?.triage || {};
  const assignment = data?.data?.assignment_recommendations || {};
  return {
    ok: data?.ok,
    verdict: data?.verdict,
    attempt_id: attempt.attempt_id,
    attempt_status: attempt.attempt_status,
    intent_id: attempt.intent_id,
    adapter_id: attempt.adapter_id,
    provider_ref: attempt.provider_ref,
    endpoint_ref: attempt.endpoint_ref,
    model_ref: attempt.model_ref,
    prompt_template_ref: data?.data?.attempt?.prompt_template_ref,
    retry_of: attempt.retry_of,
    requirement: intent.requirement,
    title: frontmatter.title,
    body: proposal.body,
    task_id: frontmatter.task_id,
    task_mode: frontmatter.task_mode,
    recommended_task_class: triage.recommended_task_class || frontmatter.task_class,
    complexity_note: frontmatter.complexity_note,
    complexity_rationale: triage.rationale,
    assigned_to: assignment.assigned_to || frontmatter.assigned_to,
    agent_instance: assignment.agent_instance || frontmatter.agent_instance,
    reviewer: assignment.reviewer,
    audit_by: assignment.audit_by,
    assumptions: triage.assumptions || [],
    missing_information: triage.missing_information || [],
    possible_owner_gates: triage.possible_owner_gates || [],
    planned_writes: data?.planned_writes || [],
    performed_writes: data?.performed_writes || [],
    blocking_reasons: data?.blocking_reasons || [],
    needs_owner_reasons: data?.needs_owner_reasons || [],
    warnings: data?.warnings || [],
  };
}

function renderAiAuthorCard(data) {
  const card = document.getElementById("ai-author-card");
  card.replaceChildren();
  if (!data) {
    card.textContent = "Enter a natural-language requirement and preview a fixture-only or live BYO-LLM draft.";
    return;
  }
  const summary = summarizeAiAuthor(data);
  const grid = document.createElement("div");
  grid.className = "ai-author-grid";
  grid.append(
    createTextBlock("ai-author-chip", "Verdict", summary.verdict),
    createTextBlock("ai-author-chip", "Attempt", summary.attempt_id),
    createTextBlock("ai-author-chip", "Task", summary.task_id),
    createTextBlock("ai-author-chip", "Class", summary.recommended_task_class),
    createTextBlock("ai-author-chip", "Assigned", summary.assigned_to),
    createTextBlock("ai-author-chip", "Instance", summary.agent_instance),
    createTextBlock("ai-author-chip", "Reviewer", summary.reviewer),
    createTextBlock("ai-author-chip", "Auditor", summary.audit_by),
    createTextBlock("ai-author-chip", "Adapter", summary.adapter_id),
    createTextBlock("ai-author-chip", "Provider", summary.provider_ref),
    createTextBlock("ai-author-chip", "Model", summary.model_ref),
    createTextBlock("ai-author-chip", "Endpoint", summary.endpoint_ref),
    createTextBlock("ai-author-chip", "Retry Of", summary.retry_of),
    createTextBlock("ai-author-chip wide", "Original Requirement", summary.requirement),
    createTextBlock("ai-author-chip wide", "Title", summary.title),
    createTextBlock("ai-author-chip wide", "Complexity", summary.complexity_note),
    createTextBlock("ai-author-chip wide", "Rationale", summary.complexity_rationale)
  );
  const body = document.createElement("pre");
  body.className = "ai-author-body";
  body.textContent = summary.body || "(no draft body)";
  card.append(
    grid,
    body,
    createListBlock("Assumptions", summary.assumptions),
    createListBlock("Missing Information", summary.missing_information),
    createListBlock("Possible Owner Gates", summary.possible_owner_gates),
    createListBlock("Planned Writes", summary.planned_writes.map((row) => row.path || String(row))),
    createListBlock("Performed Writes", summary.performed_writes.map((row) => row.path || String(row))),
    createListBlock("Owner Attention", summary.needs_owner_reasons),
    createListBlock("Safety Blocks", summary.blocking_reasons)
  );
}

function updateAiAuthorConfirmState() {
  document.getElementById("ai-author-confirm").disabled = !(
    latestAiAuthorPreview?.dry_run_id
    && latestAiAuthorPreview?.verdict !== "BLOCK"
    && document.getElementById("ai-author-owner-confirmed").checked
  );
}

function invalidateAiAuthorPreview() {
  latestAiAuthorPreview = null;
  document.getElementById("ai-author-owner-confirmed").checked = false;
  document.getElementById("ai-author-confirm").disabled = true;
  document.getElementById("ai-author-discard").disabled = true;
  renderAiAuthorCard(null);
  document.getElementById("ai-author-result").textContent = "Inputs changed. Run a fresh AI authoring preview.";
}

async function previewAiAuthorDraft() {
  const requirement = aiAuthorValue("ai-author-requirement");
  const actor = aiAuthorValue("ai-author-actor");
  if (!requirement || !actor) {
    document.getElementById("ai-author-result").textContent = JSON.stringify({ ok: false, message: "requirement and actor are required." }, null, 2);
    return null;
  }
  latestAiAuthorPreview = null;
  document.getElementById("ai-author-owner-confirmed").checked = false;
  updateAiAuthorConfirmState();
  try {
    const live = aiAuthorMode() === "live";
    const path = live ? "/api/ai-author/live/preview" : "/api/ai-author/preview";
    const payload = {
      actor,
      intent: aiAuthorIntent(),
    };
    if (live) {
      Object.assign(payload, {
        endpoint_ref: aiAuthorValue("ai-author-endpoint-ref"),
        credential_ref: aiAuthorValue("ai-author-credential-ref"),
        provider_ref: aiAuthorValue("ai-author-provider-ref"),
        model_ref: aiAuthorValue("ai-author-model-ref"),
        request_config_ref: aiAuthorValue("ai-author-request-config-ref"),
        request_timeout_seconds: Number(aiAuthorValue("ai-author-timeout-seconds")),
        max_output_tokens: Number(aiAuthorValue("ai-author-max-output-tokens")),
      });
    } else {
      payload.fixture_id = aiAuthorValue("ai-author-fixture");
    }
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    latestAiAuthorPreview = data?.dry_run_id ? data : null;
    latestDebug[path] = data;
    document.getElementById("ai-author-discard").disabled = !latestAiAuthorPreview;
    document.getElementById("ai-author-result").textContent = JSON.stringify(data, null, 2);
    renderAiAuthorCard(data);
    updateAiAuthorConfirmState();
    updateDebugPanel();
    return data;
  } catch (err) {
    document.getElementById("ai-author-result").textContent = JSON.stringify({ ok: false, message: String(err) }, null, 2);
    renderAiAuthorCard(null);
    return { ok: false, error: String(err) };
  }
}

async function confirmAiAuthorDraft() {
  if (!latestAiAuthorPreview) {
    return null;
  }
  document.getElementById("ai-author-confirm").disabled = true;
  try {
    const path = latestAiAuthorPreview?.operation === "ai_assisted_live_authoring"
      ? "/api/ai-author/live/confirm"
      : "/api/ai-author/confirm";
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        actor: aiAuthorValue("ai-author-actor"),
        owner_confirmed: document.getElementById("ai-author-owner-confirmed").checked,
        preview: latestAiAuthorPreview,
      }),
    });
    const data = await response.json();
    latestDebug[path] = data;
    document.getElementById("ai-author-result").textContent = JSON.stringify(data, null, 2);
    renderAiAuthorCard(data);
    latestAiAuthorPreview = null;
    document.getElementById("ai-author-owner-confirmed").checked = false;
    document.getElementById("ai-author-discard").disabled = true;
    updateAiAuthorConfirmState();
    updateDebugPanel();
    await refreshPanel("drafts");
    return data;
  } catch (err) {
    document.getElementById("ai-author-result").textContent = JSON.stringify({ ok: false, message: String(err) }, null, 2);
    return { ok: false, error: String(err) };
  }
}

function profileValue(id) {
  return document.getElementById(id).value.trim();
}

function profileList(id) {
  return profileValue(id).split(",").map((value) => value.trim()).filter(Boolean);
}

function profileProvenance() {
  const values = {
    vendor: profileValue("profile-provenance-vendor"),
    harness: profileValue("profile-provenance-harness"),
    model_family: profileValue("profile-provenance-model-family"),
    host: profileValue("profile-provenance-host"),
  };
  return Object.fromEntries(Object.entries(values).filter(([, value]) => value));
}

function profileInstance({ replacement = false } = {}) {
  return {
    agent_instance: replacement ? profileValue("profile-replacement-instance") : profileValue("profile-agent-instance"),
    display_name: replacement ? profileValue("profile-replacement-display-name") : profileValue("profile-display-name"),
    identity_status: replacement ? "active" : profileValue("profile-identity-status"),
    enabled: replacement ? true : document.getElementById("profile-enabled").checked,
    description: profileValue("profile-description"),
    capabilities: profileList("profile-capabilities"),
    write_scopes: profileList("profile-write-scopes"),
    default_task_modes: profileList("profile-default-task-modes"),
    model_tiers_available: profileList("profile-model-tiers"),
    context_bundles_supported: profileList("profile-context-bundles"),
    allowed_modes: profileList("profile-allowed-modes"),
    forbidden_modes: profileList("profile-forbidden-modes"),
    legacy_instance_ids: profileList("profile-legacy-ids"),
    supersedes_instance_ids: profileList("profile-supersedes-ids"),
    provenance: profileProvenance(),
  };
}

function profilePayload() {
  const action = profileValue("profile-action");
  const payload = {
    action,
    agent_id: profileValue("profile-agent-id"),
  };
  if (action === "upsert") {
    payload.instance = profileInstance();
  } else {
    payload.agent_instance = profileValue("profile-agent-instance");
    if (action === "supersede") {
      payload.replacement = profileInstance({ replacement: true });
    }
  }
  return payload;
}

function updateProfileActionFields() {
  const action = profileValue("profile-action");
  document.getElementById("profile-instance-fields").hidden = action === "deactivate";
  document.getElementById("profile-replacement-fields").hidden = action !== "supersede";
}

function renderProfileCard(data) {
  const card = document.getElementById("profile-card");
  card.replaceChildren();
  if (!data) {
    card.textContent = "Fill the structured fields and preview the workspace-local registry write.";
    return;
  }
  const summary = data?.summary || data?.data?.change_summary || {};
  const original = data?.data?.original_payload || {};
  const proposed = original.instance || original.replacement || {};
  const grid = document.createElement("div");
  grid.className = "profile-grid";
  grid.append(
    createTextBlock("ai-author-chip", "Verdict", data?.verdict),
    createTextBlock("ai-author-chip", "Action", summary.action),
    createTextBlock("ai-author-chip", "Agent ID", original.agent_id),
    createTextBlock("ai-author-chip", "Instance", summary.agent_instance || proposed.agent_instance),
    createTextBlock("ai-author-chip", "Display Name", proposed.display_name),
    createTextBlock("ai-author-chip", "Target", data?.data?.target_path)
  );
  card.append(
    grid,
    createListBlock("Changed Fields", summary.changed_fields || []),
    createListBlock("Owner-visible Fields", summary.owner_visible_fields || []),
    createListBlock("Planned Writes", (data?.planned_writes || []).map((row) => row.path || String(row))),
    createListBlock("Owner Attention", data?.needs_owner_reasons || []),
    createListBlock("Warnings", data?.warnings || []),
    createListBlock("Safety Blocks", data?.blocking_reasons || [])
  );
}

function updateProfileConfirmState() {
  document.getElementById("profile-confirm").disabled = !(
    latestProfilePreview?.dry_run_id
    && latestProfilePreview?.verdict !== "BLOCK"
    && document.getElementById("profile-owner-confirmed").checked
  );
}

function invalidateProfilePreview() {
  latestProfilePreview = null;
  document.getElementById("profile-owner-confirmed").checked = false;
  document.getElementById("profile-confirm").disabled = true;
  document.getElementById("profile-discard").disabled = true;
  renderProfileCard(null);
  document.getElementById("profile-result").textContent = "Inputs changed. Run a fresh profile preview.";
}

async function previewProfileDraft() {
  const actor = profileValue("profile-actor");
  const payload = profilePayload();
  if (!actor || !payload.agent_id || !profileValue("profile-agent-instance")) {
    document.getElementById("profile-result").textContent = JSON.stringify({ ok: false, message: "actor, agent_id, and canonical agent_instance are required." }, null, 2);
    return null;
  }
  latestProfilePreview = null;
  document.getElementById("profile-owner-confirmed").checked = false;
  updateProfileConfirmState();
  try {
    const response = await fetch("/api/agent-profile/draft", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actor, payload }),
    });
    const data = await response.json();
    latestProfilePreview = data?.dry_run_id ? data : null;
    latestDebug["/api/agent-profile/draft"] = data;
    document.getElementById("profile-discard").disabled = !latestProfilePreview;
    document.getElementById("profile-result").textContent = JSON.stringify(data, null, 2);
    renderProfileCard(data);
    updateProfileConfirmState();
    updateDebugPanel();
    return data;
  } catch (err) {
    document.getElementById("profile-result").textContent = JSON.stringify({ ok: false, message: String(err) }, null, 2);
    renderProfileCard(null);
    return { ok: false, error: String(err) };
  }
}

async function confirmProfileDraft() {
  if (!latestProfilePreview) {
    return null;
  }
  document.getElementById("profile-confirm").disabled = true;
  try {
    const response = await fetch("/api/agent-profile/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        actor: profileValue("profile-actor"),
        owner_confirmed: document.getElementById("profile-owner-confirmed").checked,
        preview: latestProfilePreview,
      }),
    });
    const data = await response.json();
    latestDebug["/api/agent-profile/confirm"] = data;
    document.getElementById("profile-result").textContent = JSON.stringify(data, null, 2);
    renderProfileCard(data);
    latestProfilePreview = null;
    document.getElementById("profile-owner-confirmed").checked = false;
    document.getElementById("profile-discard").disabled = true;
    updateProfileConfirmState();
    updateDebugPanel();
    await refreshPanel("agents");
    return data;
  } catch (err) {
    document.getElementById("profile-result").textContent = JSON.stringify({ ok: false, message: String(err) }, null, 2);
    return { ok: false, error: String(err) };
  }
}

function agentRows(data) {
  const profiles = Array.isArray(data?.data?.profiles) ? data.data.profiles : [];
  const rows = [];
  for (const profile of profiles) {
    rows.push({ ...profile, agent_row_kind: "profile" });
    const instances = Array.isArray(profile.instances) ? profile.instances : [];
    for (const instance of instances) {
      rows.push({ ...instance, parent_agent_id: profile.agent_id, agent_row_kind: "instance" });
    }
  }
  return rows;
}

function valueOrDash(value) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  return value;
}

function rowTitle(row) {
  return row?.metadata?.title || row?.title || row?.summary?.title || "-";
}

function rowStatus(row) {
  return row?.metadata?.status || row?.status || "-";
}

function rowPriority(row) {
  return row?.metadata?.priority || row?.priority || "-";
}

function rowAssignedTo(row) {
  return row?.metadata?.assigned_to || row?.assigned_to || "-";
}

function rowAgentInstance(row) {
  return row?.metadata?.agent_instance || row?.agent_instance || "-";
}

function rowProject(row) {
  return row?.metadata?.project || row?.project || "-";
}

function rowRecommendedAction(row) {
  return row?.recommended_action || row?.preview?.recommended_action || "-";
}

function taskLabel(task) {
  return [task.task_id, task.effective_task_class || "simple", task.metadata?.title || task.title, task.path].filter(Boolean).join(" | ");
}

function renderTaskList(queueData) {
  const list = document.getElementById("task-list");
  const tasks = taskRows(queueData);
  list.replaceChildren();
  selectedTask = null;
  if (tasks.length === 0) {
    list.textContent = "No tasks found.";
    return;
  }
  for (const task of tasks.slice(0, 25)) {
    const button = document.createElement("button");
    button.className = "task-button";
    button.textContent = taskLabel(task);
    button.title = task.path || task.task_id || "";
    button.addEventListener("click", () => selectTask(task, button));
    list.appendChild(button);
  }
}

function detailLabel(row) {
  return [row?.task_id, rowTitle(row), row?.path].filter(Boolean).join(" | ");
}

function recordLabel(row) {
  const id = row?.decision_id || row?.session_id || row?.claim_id || row?.id || "-";
  return [row?.record_kind, id, row?.task_id, row?.path].filter(Boolean).join(" | ");
}

function externalIntakeLabel(row) {
  return [row?.task_id, row?.client_tag, row?.source_tag, row?.title, row?.path].filter(Boolean).join(" | ");
}

function ownerDecisionRecordLabel(row) {
  return [row?.decision_id, row?.decision_status, row?.client_tag || row?.project, row?.draft_path || row?.task_id || row?.path].filter(Boolean).join(" | ");
}

function agentLabel(row) {
  const id = row?.agent_id || row?.logical_agent_id || row?.agent_instance || row?.name || "-";
  const kind = row?.agent_row_kind || "agent";
  return [kind, id, row?.runtime_profile].filter(Boolean).join(" | ");
}

function renderDetailButton(list, row, onSelect) {
  const button = document.createElement("button");
  button.className = "task-button";
  button.textContent = detailLabel(row);
  button.title = row?.path || row?.task_id || "";
  button.addEventListener("click", () => onSelect(row, button));
  list.appendChild(button);
}

function clearSelected(container) {
  for (const button of container.querySelectorAll(".selected")) {
    button.classList.remove("selected");
  }
}

function renderNeedsOwnerDetails(data) {
  const list = document.getElementById("needs-owner-list");
  const rows = envelopeRows(data);
  list.replaceChildren();
  selectedNeedsOwner = null;
  document.getElementById("needs-owner-load-task").disabled = true;
  document.getElementById("needs-owner-detail").textContent = rows.length === 0 ? "No needs-owner items." : "Select a needs-owner item.";
  for (const row of rows) {
    renderDetailButton(list, row, selectNeedsOwner);
  }
}

function renderValidationDetails(data) {
  const list = document.getElementById("validation-list");
  const rows = envelopeRows(data);
  list.replaceChildren();
  selectedValidation = null;
  document.getElementById("validation-load-task").disabled = true;
  document.getElementById("validation-detail").textContent = rows.length === 0 ? "No validation rows." : "Select a validation row.";
  for (const row of rows) {
    renderDetailButton(list, row, selectValidation);
  }
}

function renderRecordsDetails(data) {
  const list = document.getElementById("records-list");
  const rows = recordRows(data);
  list.replaceChildren();
  selectedRecord = null;
  document.getElementById("records-detail").textContent = rows.length === 0 ? "No records found." : "Select a session record or claim log.";
  for (const row of rows) {
    const button = document.createElement("button");
    button.className = "task-button";
    button.textContent = recordLabel(row);
    button.title = row?.path || row?.session_id || row?.claim_id || "";
    button.addEventListener("click", () => selectRecord(row, button));
    list.appendChild(button);
  }
}

function renderExternalIntakeReview(data) {
  const list = document.getElementById("external-intake-review-list");
  const rows = externalIntakeRows(data);
  list.replaceChildren();
  selectedExternalIntake = null;
  renderExternalIntakeCard(null);
  document.getElementById("external-intake-prefill-resolution").disabled = true;
  document.getElementById("external-intake-load-publish").disabled = true;
  document.getElementById("external-intake-review-detail").textContent = rows.length === 0 ? "No external intake drafts found." : "Select an external intake draft.";
  for (const row of rows) {
    const button = document.createElement("button");
    button.className = "task-button";
    button.textContent = externalIntakeLabel(row);
    button.title = row?.path || row?.task_id || "";
    button.addEventListener("click", () => selectExternalIntake(row, button));
    list.appendChild(button);
  }
}

function renderOwnerDecisionRecords(data) {
  const list = document.getElementById("owner-decision-records-list");
  const rows = ownerDecisionRecordRows(data);
  list.replaceChildren();
  selectedOwnerDecisionRecord = null;
  renderOwnerDecisionRecordCard(null);
  document.getElementById("owner-decision-record-prefill-resolution").disabled = true;
  document.getElementById("owner-decision-records-detail").textContent = rows.length === 0 ? "No owner decision records found." : "Select an owner decision record.";
  for (const row of rows) {
    const button = document.createElement("button");
    button.className = "task-button";
    button.textContent = ownerDecisionRecordLabel(row);
    button.title = row?.path || row?.decision_id || "";
    button.addEventListener("click", () => selectOwnerDecisionRecord(row, button));
    list.appendChild(button);
  }
}

function renderExternalIntakeCard(row) {
  const card = document.getElementById("external-intake-review-card");
  card.replaceChildren();
  if (!row) {
    card.textContent = "Select an external intake draft.";
    return;
  }
  card.append(
    createTextBlock("intake-review-chip", "Task", row.task_id),
    createTextBlock("intake-review-chip", "Client", row.client_tag),
    createTextBlock("intake-review-chip", "Source", row.source_tag),
    createTextBlock("intake-review-chip", "External Ref", row.external_ref),
    createTextBlock("intake-review-chip", "Priority", row.priority),
    createTextBlock("intake-review-chip", "Needs Owner", row.needs_owner),
    createTextBlock("intake-review-chip", "Verdict", row.verdict),
    createTextBlock("intake-review-chip wide", "Title", row.title),
    createTextBlock("intake-review-chip wide", "Path", row.path)
  );
}

function renderOwnerDecisionRecordCard(row) {
  const card = document.getElementById("owner-decision-records-card");
  card.replaceChildren();
  if (!row) {
    card.textContent = "Select an owner decision record.";
    return;
  }
  card.append(
    createTextBlock("owner-decision-record-chip", "Decision", row.decision_id),
    createTextBlock("owner-decision-record-chip", "Status", row.decision_status),
    createTextBlock("owner-decision-record-chip", "Type", row.decision_type),
    createTextBlock("owner-decision-record-chip", "Client", row.client_tag || row.project),
    createTextBlock("owner-decision-record-chip", "Evidence", row.evidence_id),
    createTextBlock("owner-decision-record-chip", "External Ref", row.external_ref),
    createTextBlock("owner-decision-record-chip wide", "Draft", row.draft_path),
    createTextBlock("owner-decision-record-chip wide", "Path", row.path)
  );
}

function renderAgentsDetails(data) {
  const list = document.getElementById("agents-list");
  const rows = agentRows(data);
  list.replaceChildren();
  selectedAgent = null;
  document.getElementById("agents-detail").textContent = rows.length === 0 ? "No agent profiles or instances found." : "Select an agent profile or runtime instance.";
  for (const row of rows) {
    const button = document.createElement("button");
    button.className = "task-button";
    button.textContent = agentLabel(row);
    button.title = row?.agent_id || row?.agent_instance || "";
    button.addEventListener("click", () => selectAgent(row, button));
    list.appendChild(button);
  }
}

function renderGovernancePanel(data) {
  const card = document.getElementById("governance-card");
  card.replaceChildren();
  if (!data?.ok && !data?.data) {
    card.textContent = "Governance files could not be loaded.";
    return;
  }
  const docs = Array.isArray(data?.data?.documents) ? data.data.documents : [];
  if (docs.length === 0) {
    card.textContent = "No governance documents found for 2_projects/lybra/.";
    return;
  }
  const meta = document.createElement("div");
  meta.className = "governance-meta";
  meta.append(
    createTextBlock("governance-chip", "Project", data?.data?.project || "lybra"),
    createTextBlock("governance-chip", "Present", `${data?.summary?.documents_present ?? 0}/${data?.summary?.documents_total ?? docs.length}`),
    createTextBlock("governance-chip", "Writes", data?.data?.writes_enabled ? "enabled" : "disabled")
  );
  card.appendChild(meta);
  for (const doc of docs) {
    const section = document.createElement("article");
    section.className = "governance-doc";
    const title = document.createElement("h3");
    title.textContent = doc.path || doc.name || "governance document";
    const facts = document.createElement("p");
    facts.className = "governance-doc-meta";
    if (doc.exists && doc.is_file) {
      facts.textContent = `${doc.line_count || 0} lines | ${doc.byte_size || 0} bytes${doc.truncated ? " | latest excerpt" : ""}`;
    } else {
      facts.textContent = "Missing";
    }
    const excerpt = document.createElement("pre");
    excerpt.className = "governance-excerpt";
    excerpt.textContent = doc.exists && doc.is_file ? (doc.excerpt || "(empty file)") : "File is not present in this workspace.";
    section.append(title, facts, excerpt);
    card.appendChild(section);
  }
}

function renderOrchestrationAvailability(data) {
  latestOrchestrationIndex = data;
  const entries = Array.isArray(data?.data?.entries) ? data.data.entries : [];
  const targetIds = [
    "orchestration-summary-availability",
    "orchestration-timeline-availability",
    "planner-loop-availability",
  ];
  const message = entries.length
    ? `Available orchestration ids: ${entries.map((entry) => entry.orchestration_id).join(", ")}`
    : "No orchestration ids found in 5_tasks/orchestration/. Summary, timeline, and planner loop previews can be skipped for this workspace until orchestration data exists.";
  for (const id of targetIds) {
    const el = document.getElementById(id);
    el.textContent = message;
    el.classList.toggle("warn", entries.length === 0);
  }
  if (entries.length === 1) {
    const value = entries[0].orchestration_id;
    for (const id of ["orchestration-summary-id", "orchestration-timeline-id", "planner-loop-id"]) {
      const input = document.getElementById(id);
      if (!input.value.trim()) {
        input.placeholder = value;
      }
    }
  }
}

async function loadOrchestrationIndex() {
  try {
    const response = await fetch("/api/orchestration/index", { cache: "no-store" });
    const data = await response.json();
    latestDebug["/api/orchestration/index"] = data;
    renderOrchestrationAvailability(data);
    updateDebugPanel();
    return data;
  } catch (err) {
    const data = { ok: false, error: String(err) };
    latestDebug["/api/orchestration/index"] = data;
    renderOrchestrationAvailability(data);
    updateDebugPanel();
    return data;
  }
}

function summarizeAttentionRow(row) {
  return {
    task_id: valueOrDash(row?.task_id),
    title: rowTitle(row),
    path: valueOrDash(row?.path),
    queue_state: valueOrDash(row?.queue_state),
    status: rowStatus(row),
    verdict: valueOrDash(row?.verdict),
    priority: rowPriority(row),
    assigned_to: rowAssignedTo(row),
    agent_instance: rowAgentInstance(row),
    project: rowProject(row),
    needs_owner_reasons: row?.needs_owner_reasons || [],
    blocking_reasons: row?.blocking_reasons || [],
    warnings: row?.warnings || [],
    recommended_action: rowRecommendedAction(row),
  };
}

function summarizeValidationRow(row, envelope) {
  return {
    summary: envelope?.summary || envelope?.data?.summary || {},
    total_tasks: envelope?.summary?.total_tasks || envelope?.data?.summary?.total_tasks || envelopeRows(envelope).length,
    verdict_counts: envelope?.summary?.verdict_counts || envelope?.data?.summary?.verdict_counts || {},
    task_id: valueOrDash(row?.task_id),
    path: valueOrDash(row?.path),
    queue_state: valueOrDash(row?.queue_state),
    status: rowStatus(row),
    verdict: valueOrDash(row?.verdict),
    blocking_reasons: row?.blocking_reasons || [],
    warnings: row?.warnings || [],
    needs_owner_reasons: row?.needs_owner_reasons || [],
    recommended_action: rowRecommendedAction(row),
  };
}

function summarizeRecord(row, envelope) {
  const summary = envelope?.summary || envelope?.data?.summary || {};
  return {
    record_kind: valueOrDash(row?.record_kind),
    total_session_records: valueOrDash(summary.session_records),
    total_claim_logs: valueOrDash(summary.claim_logs),
    total_owner_decision_records: valueOrDash(summary.owner_decision_records),
    parse_errors_count: valueOrDash(summary.parse_errors),
    warnings_count: Array.isArray(envelope?.warnings) ? envelope.warnings.length : 0,
    decision_id: valueOrDash(row?.decision_id),
    decision_status: valueOrDash(row?.decision_status),
    decision_type: valueOrDash(row?.decision_type),
    session_id: valueOrDash(row?.session_id),
    claim_id: valueOrDash(row?.claim_id),
    task_id: valueOrDash(row?.task_id),
    actor: valueOrDash(row?.actor || row?.claimed_by || row?.started_by),
    claimed_by: valueOrDash(row?.claimed_by),
    started_by: valueOrDash(row?.started_by),
    claimed_at: valueOrDash(row?.claimed_at),
    status: valueOrDash(row?.status),
    path: valueOrDash(row?.path),
  };
}

function summarizeExternalIntake(row, envelope) {
  const summary = envelope?.summary || {};
  return {
    total_external_intake_drafts: valueOrDash(summary.total),
    ready: valueOrDash(summary.ready),
    blocked: valueOrDash(summary.blocked),
    task_id: valueOrDash(row?.task_id),
    title: row?.title || "-",
    client_tag: valueOrDash(row?.client_tag),
    source_tag: valueOrDash(row?.source_tag),
    external_ref: valueOrDash(row?.external_ref),
    priority: valueOrDash(row?.priority),
    needs_owner: valueOrDash(row?.needs_owner),
    verdict: valueOrDash(row?.verdict),
    path: valueOrDash(row?.path),
  };
}

function summarizeOwnerDecisionRecord(row, envelope) {
  const summary = envelope?.summary || {};
  return {
    total_owner_decision_records: valueOrDash(summary.total),
    approved: valueOrDash(summary.approved),
    needs_revision: valueOrDash(summary.needs_revision),
    rejected: valueOrDash(summary.rejected),
    decision_id: valueOrDash(row?.decision_id),
    decision_status: valueOrDash(row?.decision_status),
    decision_type: valueOrDash(row?.decision_type),
    decided_at: valueOrDash(row?.decided_at),
    captured_by: valueOrDash(row?.captured_by),
    capture_surface: valueOrDash(row?.capture_surface),
    project: valueOrDash(row?.project),
    task_id: valueOrDash(row?.task_id),
    draft_path: valueOrDash(row?.draft_path),
    external_ref: valueOrDash(row?.external_ref),
    evidence_id: valueOrDash(row?.evidence_id),
    source_tag: valueOrDash(row?.source_tag),
    client_tag: valueOrDash(row?.client_tag),
    path: valueOrDash(row?.path),
  };
}

function summarizeAgent(row, envelope) {
  const summary = envelope?.summary || envelope?.data?.summary || {};
  const instances = Array.isArray(row?.instances) ? row.instances : [];
  const aliases = Array.isArray(row?.aliases) ? row.aliases : [];
  return {
    row_kind: valueOrDash(row?.agent_row_kind),
    profile_count: valueOrDash(summary.profiles),
    instance_count: valueOrDash(summary.instances),
    warning_count: valueOrDash(summary.warnings),
    agent_id: valueOrDash(row?.agent_id || row?.logical_agent_id || row?.name),
    role: valueOrDash(row?.role),
    agent_instance: valueOrDash(row?.agent_instance),
    runtime_profile: valueOrDash(row?.runtime_profile),
    runtime_kind: valueOrDash(row?.runtime_kind || row?.runtime_entrypoint),
    availability_status: valueOrDash(row?.availability_status),
    enabled: valueOrDash(row?.enabled),
    aliases_count: aliases.length,
    aliases,
    instances_count: instances.length,
    runtime_command: valueOrDash(row?.runtime_command),
    runtime_args: row?.runtime_args || [],
  };
}

function setTaskSelectorFromRow(row) {
  document.getElementById("task-selector").value = row?.task_id || row?.path || "";
}

function selectNeedsOwner(row, sourceButton) {
  selectedNeedsOwner = row;
  clearSelected(document.getElementById("needs-owner-list"));
  sourceButton?.classList?.add("selected");
  document.getElementById("needs-owner-detail").textContent = JSON.stringify(summarizeAttentionRow(row), null, 2);
  document.getElementById("needs-owner-load-task").disabled = !(row?.task_id || row?.path);
}

function selectValidation(row, sourceButton) {
  selectedValidation = row;
  clearSelected(document.getElementById("validation-list"));
  sourceButton?.classList?.add("selected");
  const envelope = latestDebug["/api/validate"] || {};
  document.getElementById("validation-detail").textContent = JSON.stringify(summarizeValidationRow(row, envelope), null, 2);
  document.getElementById("validation-load-task").disabled = !(row?.task_id || row?.path);
}

function selectRecord(row, sourceButton) {
  selectedRecord = row;
  clearSelected(document.getElementById("records-list"));
  sourceButton?.classList?.add("selected");
  const envelope = latestDebug["/api/records"] || {};
  document.getElementById("records-detail").textContent = JSON.stringify(summarizeRecord(row, envelope), null, 2);
}

function selectExternalIntake(row, sourceButton) {
  selectedExternalIntake = row;
  clearSelected(document.getElementById("external-intake-review-list"));
  sourceButton?.classList?.add("selected");
  const envelope = latestDebug["/api/external-intake/review"] || {};
  renderExternalIntakeCard(row);
  document.getElementById("external-intake-prefill-resolution").disabled = false;
  document.getElementById("external-intake-load-publish").disabled = false;
  document.getElementById("external-intake-review-detail").textContent = JSON.stringify(summarizeExternalIntake(row, envelope), null, 2);
}

function selectOwnerDecisionRecord(row, sourceButton) {
  selectedOwnerDecisionRecord = row;
  clearSelected(document.getElementById("owner-decision-records-list"));
  sourceButton?.classList?.add("selected");
  const envelope = latestDebug["/api/owner-decision-records"] || {};
  renderOwnerDecisionRecordCard(row);
  document.getElementById("owner-decision-record-prefill-resolution").disabled = false;
  document.getElementById("owner-decision-records-detail").textContent = JSON.stringify(summarizeOwnerDecisionRecord(row, envelope), null, 2);
}

function selectAgent(row, sourceButton) {
  selectedAgent = row;
  clearSelected(document.getElementById("agents-list"));
  sourceButton?.classList?.add("selected");
  const envelope = latestDebug["/api/agents"] || {};
  document.getElementById("agents-detail").textContent = JSON.stringify(summarizeAgent(row, envelope), null, 2);
}

async function loadTaskFromNeedsOwner() {
  if (!selectedNeedsOwner) {
    return null;
  }
  setTaskSelectorFromRow(selectedNeedsOwner);
  return loadTaskDetail();
}

async function loadTaskFromValidation() {
  if (!selectedValidation) {
    return null;
  }
  setTaskSelectorFromRow(selectedValidation);
  return loadTaskDetail();
}

function taskQuery() {
  const raw = document.getElementById("task-selector").value.trim();
  if (!raw) {
    return "";
  }
  const key = raw.includes("/") ? "path" : "task_id";
  return `${key}=${encodeURIComponent(raw)}`;
}

function selectTask(task, sourceButton) {
  selectedTask = task;
  clearSelected(document.getElementById("task-list"));
  sourceButton?.classList?.add("selected");
  document.getElementById("task-selector").value = task.task_id || task.path || "";
  loadTaskDetail();
}

function summarizeTaskDetail(data) {
  const task = data.data || {};
  return {
    ok: data.ok,
    verdict: data.verdict,
    task_id: task.task_id,
    title: task.metadata?.title || task.title,
    path: task.path,
    queue_state: task.queue_state,
    status: task.metadata?.status,
    assigned_to: task.metadata?.assigned_to,
    agent_instance: task.metadata?.agent_instance,
    task_mode: task.metadata?.task_mode,
    task_class: task.metadata?.task_class,
    effective_task_class: task.effective_task_class,
    complexity_note: task.metadata?.complexity_note,
    warnings: data.warnings,
    blocking_reasons: data.blocking_reasons,
  };
}

function summarizePreview(data) {
  const preview = data.data || {};
  return {
    task_id: data.summary?.task_id || preview.task_id,
    title: preview.title || preview.task?.title,
    actor: data.actor?.actor,
    verdict: data.verdict,
    can_start_session: data.summary?.can_start_session || preview.can_start_session,
    actor_match: data.actor_match || preview.actor_match,
    blocking_reasons: data.blocking_reasons,
    warnings: data.warnings,
    needs_owner_reasons: data.needs_owner_reasons,
    recommended_action: preview.recommended_action,
    task_mode: preview.task_mode,
    task_class: preview.task_class,
    effective_task_class: preview.effective_task_class,
    complexity_note: preview.complexity_note,
    availability_warning: preview.availability_warning,
  };
}

function summarizeExecute(data) {
  return {
    ok: data?.ok,
    verdict: data?.verdict,
    operation: data?.operation,
    actor: data?.actor?.actor,
    dry_run_id: data?.dry_run_id,
    dry_run_snapshot_hash: data?.dry_run_snapshot_hash,
    execute_allowed: data?.execute_allowed,
    execute_blocking_reasons: data?.execute_blocking_reasons || [],
    owner_confirmation_required: data?.owner_confirmation_required,
    owner_confirmation_reasons: data?.owner_confirmation_reasons || [],
    planned_writes: data?.planned_writes || [],
    planned_moves: data?.planned_moves || [],
    performed_writes: data?.performed_writes || [],
    performed_moves: data?.performed_moves || [],
    source_path: data?.data?.source_path,
    target_path: data?.data?.target_path,
    rendered_markdown: data?.data?.rendered_markdown,
    warnings: data?.warnings || [],
    blocking_reasons: data?.blocking_reasons || [],
    errors: data?.errors || [],
    summary: data?.summary,
  };
}

function formatPublishList(values) {
  const rows = Array.isArray(values) ? values : [];
  return rows.map((value) => {
    if (!value || typeof value !== "object") {
      return value;
    }
    return value.path || value.source_path || value.target_path || JSON.stringify(value);
  });
}

function renderDraftPublishCard(data, phase) {
  const card = document.getElementById("draft-publish-card");
  card.replaceChildren();
  if (!data) {
    card.textContent = "Enter a draft path and run dry-run.";
    return;
  }
  const summary = summarizeExecute(data);
  const canExecute = Boolean(data?.dry_run_id && data?.execute_allowed && data?.verdict !== "BLOCK");
  const status = phase === "confirm"
    ? (data?.ok ? "published" : "confirm failed")
    : (canExecute ? "ready for Owner confirm" : "blocked or needs review");
  const header = document.createElement("div");
  header.className = "publish-review-header";
  header.append(
    createTextBlock("publish-review-chip", "Status", status),
    createTextBlock("publish-review-chip", "Verdict", summary.verdict),
    createTextBlock("publish-review-chip", "Execute", summary.execute_allowed ? "allowed" : "blocked"),
    createTextBlock("publish-review-chip", "Dry-run token", summary.dry_run_id)
  );

  const paths = document.createElement("div");
  paths.className = "publish-review-grid";
  paths.append(
    createTextBlock("publish-review-chip wide", "Source", summary.source_path),
    createTextBlock("publish-review-chip wide", "Target", summary.target_path),
    createTextBlock("publish-review-chip", "Owner confirm", summary.owner_confirmation_required ? "required" : "not required"),
    createTextBlock("publish-review-chip", "Snapshot", summary.dry_run_snapshot_hash)
  );

  const details = document.createElement("div");
  details.className = "publish-review-grid";
  details.append(
    createListBlock("Blocking Reasons", summary.execute_blocking_reasons.concat(summary.blocking_reasons || [])),
    createListBlock("Owner Confirmation Reasons", summary.owner_confirmation_reasons),
    createListBlock("Planned Moves", formatPublishList(summary.planned_moves)),
    createListBlock("Performed Moves", formatPublishList(summary.performed_moves)),
    createListBlock("Warnings", summary.warnings),
    createListBlock("Errors", summary.errors)
  );

  card.append(header, paths, details);
}

function draftValue(id) {
  return document.getElementById(id).value.trim();
}

function draftCreatePayload() {
  const actor = document.getElementById("preview-actor").value.trim();
  const frontmatter = {
    task_id: draftValue("draft-task-id"),
    title: draftValue("draft-title"),
    project: "ai-project-os",
    assigned_to: draftValue("draft-assigned-to"),
    agent_instance: draftValue("draft-agent-instance"),
    context_bundle: draftValue("draft-context-bundle"),
    task_mode: draftValue("draft-task-mode"),
    task_class: draftValue("draft-task-class"),
    complexity_note: draftValue("draft-complexity-note"),
    model_tier: draftValue("draft-model-tier"),
    priority: "medium",
    status: "pending",
    created_by: actor,
    needs_owner: false,
    output_target: draftValue("draft-output-target"),
    artifact_policy: draftValue("draft-artifact-policy"),
    task_type: "one_shot",
    polling_mode: "agent_polling",
    claim_policy: "assigned_agent_only",
    report_mode: "forum_reply",
    recurrence: "none",
  };
  return {
    operation: "draft_create",
    actor,
    payload: {
      frontmatter,
      body: document.getElementById("draft-body").value,
    },
  };
}

function draftPublishPayload() {
  const actor = document.getElementById("preview-actor").value.trim();
  return {
    operation: "draft_publish",
    actor,
    path: document.getElementById("draft-publish-path").value.trim(),
  };
}

function plannerDraftReviewPayload() {
  return {
    actor: document.getElementById("preview-actor").value.trim(),
    path: document.getElementById("planner-draft-path").value.trim(),
  };
}

function approvedPlannerDraftPublishPayload() {
  return {
    actor: document.getElementById("preview-actor").value.trim(),
    path: document.getElementById("approved-planner-draft-path").value.trim(),
  };
}

function parentRequirementPayload() {
  return {
    title: document.getElementById("parent-title").value.trim(),
    project: document.getElementById("parent-project").value.trim(),
    forum_thread_ref: document.getElementById("parent-forum-ref").value.trim(),
    planner_agent: document.getElementById("parent-planner-agent").value.trim(),
    planner_agent_instance: document.getElementById("parent-planner-instance").value.trim(),
    planner_runtime_profile: document.getElementById("parent-runtime-profile").value.trim(),
    planner_model_tier: document.getElementById("parent-model-tier").value.trim(),
    max_iterations: document.getElementById("parent-max-iterations").value.trim(),
    owner_goal: document.getElementById("parent-owner-goal").value.trim(),
  };
}

function plannerTickPayload() {
  return {
    orchestration_id: document.getElementById("tick-orchestration-id").value.trim(),
    parent_task_id: document.getElementById("tick-parent-task-id").value.trim(),
    forum_thread_ref: document.getElementById("tick-forum-ref").value.trim(),
    planner_agent: document.getElementById("tick-planner-agent").value.trim(),
    planner_agent_instance: document.getElementById("tick-planner-instance").value.trim(),
    planner_model_tier: document.getElementById("tick-model-tier").value.trim(),
    iteration_number: document.getElementById("tick-iteration-number").value.trim(),
    decision: document.getElementById("tick-decision").value.trim(),
    decision_reason: document.getElementById("tick-decision-reason").value.trim(),
    next_expected_action: document.getElementById("tick-next-action").value.trim(),
    combined_planner_executor: document.getElementById("tick-combined-mode").checked,
    audit_handoff_needed: document.getElementById("tick-audit-handoff").checked,
    inputs_read: document.getElementById("tick-inputs-read").value,
    observations: document.getElementById("tick-observations").value,
    needs_owner_reasons: document.getElementById("tick-needs-owner").value,
    publish_candidates: document.getElementById("tick-publish-candidates").value,
    repair_recommendations: document.getElementById("tick-repair-recommendations").value,
    stop_condition_hits: document.getElementById("tick-stop-conditions").value,
  };
}

function ownerDecisionResolutionPayload() {
  return {
    request_id: document.getElementById("owner-resolution-request-id").value.trim(),
    orchestration_id: document.getElementById("owner-resolution-orchestration-id").value.trim(),
    actor: document.getElementById("owner-resolution-actor").value.trim(),
    forum_thread_ref: document.getElementById("owner-resolution-forum-ref").value.trim(),
    evidence_ref: document.getElementById("owner-resolution-evidence-ref").value.trim(),
    decision_type: document.getElementById("owner-resolution-decision-type").value.trim(),
    related_task_id: document.getElementById("owner-resolution-related-task-id").value.trim(),
    related_iteration_id: document.getElementById("owner-resolution-related-iteration-id").value.trim(),
    decision: document.getElementById("owner-resolution-decision").value.trim(),
    decision_reason: document.getElementById("owner-resolution-reason").value.trim(),
  };
}

function ownerRecordValue(id) {
  return document.getElementById(id).value.trim();
}

function isoNow() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

function isoFuture(days) {
  const value = new Date();
  value.setDate(value.getDate() + days);
  return value.toISOString().replace(/\.\d{3}Z$/, "Z");
}

function ownerDecisionRecordPayload() {
  const actor = ownerRecordValue("owner-record-actor");
  const project = ownerRecordValue("owner-record-project");
  const taskId = ownerRecordValue("owner-record-task-id");
  const draftPath = ownerRecordValue("owner-record-draft-path");
  const externalRef = ownerRecordValue("owner-record-external-ref");
  const evidenceId = ownerRecordValue("owner-record-evidence-id");
  const sourceTag = ownerRecordValue("owner-record-source-tag") || "board";
  const decisionId = ownerRecordValue("owner-record-decision-id");
  const decisionType = ownerRecordValue("owner-record-decision-type");
  return {
    operation: "owner_decision_record",
    actor,
    payload: {
      decision_id: decisionId,
      decision_type: decisionType,
      decision_status: ownerRecordValue("owner-record-decision-status"),
      decided_at: isoNow(),
      decided_by_ref: ownerRecordValue("owner-record-decided-by"),
      captured_by: actor,
      capture_surface: "board",
      decision_summary: ownerRecordValue("owner-record-summary"),
      decision_rationale: ownerRecordValue("owner-record-rationale"),
      applies_to: {
        project,
        task_id: taskId || null,
        draft_path: draftPath || null,
        orchestration_id: null,
        iteration_id: null,
        event_id: null,
        external_ref: externalRef || null,
      },
      approval_scope: {
        operation: "owner_decision_record",
        authority_boundary: "Board controlled execute owner_decision_record only",
        allowed_next_action: "review_external_intake_draft",
        expires_at: isoFuture(30),
      },
      owner_approval_evidence: {
        evidence_id: evidenceId,
        source_tag: sourceTag,
        client_tag: project,
        external_ref: externalRef || evidenceId || decisionId,
        approval_actor_ref: ownerRecordValue("owner-record-decided-by"),
        approval_timestamp: isoNow(),
        approval_intent: "record_owner_decision",
        evidence_hash: `board-ui:${evidenceId || decisionId}`,
        evidence_ref: evidenceId || externalRef || null,
        captured_by: actor,
        capture_method: "board_controlled_execute",
        redaction_status: "redacted_or_normalized",
        refs: [externalRef, draftPath, taskId].filter(Boolean),
      },
      refs: [externalRef, draftPath, taskId].filter(Boolean),
      capability_scope: {
        token_ref: "board_owner_decision_record",
        operations: ["owner_decision_record"],
        projects: project ? [project] : [],
        expires_at: isoFuture(30),
        evidence_ref: evidenceId || externalRef || null,
      },
    },
  };
}

function setOwnerResolutionFields(values) {
  for (const [id, value] of Object.entries(values)) {
    const el = document.getElementById(id);
    if (!el || value === undefined || value === null) {
      continue;
    }
    el.value = String(value);
  }
}

function prefillOwnerResolutionFromExternalIntake() {
  const row = selectedExternalIntake;
  const result = document.getElementById("owner-resolution-result");
  if (!row) {
    result.textContent = JSON.stringify({ ok: false, message: "Select an external intake draft first." }, null, 2);
    return;
  }
  const evidenceRef = row.external_ref || row.path || row.task_id || "";
  setOwnerResolutionFields({
    "owner-resolution-request-id": row.task_id || row.path || "",
    "owner-resolution-orchestration-id": "",
    "owner-resolution-decision-type": "external_intake_review",
    "owner-resolution-related-task-id": row.task_id || "",
    "owner-resolution-related-iteration-id": "",
    "owner-resolution-forum-ref": row.external_ref || row.path || "",
    "owner-resolution-evidence-ref": evidenceRef ? `external_intake:${evidenceRef}` : "",
    "owner-resolution-reason": [
      row.title || "External intake draft selected.",
      `client_tag=${valueOrDash(row.client_tag)}`,
      `source_tag=${valueOrDash(row.source_tag)}`,
      `path=${valueOrDash(row.path)}`,
    ].join("\n"),
  });
  result.textContent = JSON.stringify({
    ok: true,
    source: "external_intake",
    message: "External intake draft copied into Owner Decision Resolution Review. Review before submitting.",
    task_id: row.task_id,
    path: row.path,
  }, null, 2);
}

function loadExternalIntakeIntoDraftPublish() {
  const row = selectedExternalIntake;
  const result = document.getElementById("draft-publish-result");
  if (!row?.path) {
    result.textContent = JSON.stringify({ ok: false, message: "Select an external intake draft first." }, null, 2);
    return;
  }
  document.getElementById("draft-publish-path").value = row.path;
  latestDraftPublishDryRun = null;
  document.getElementById("draft-publish-confirm").disabled = true;
  renderDraftPublishCard({
    ok: true,
    verdict: "READY_FOR_DRY_RUN",
    operation: "draft_publish",
    data: { source_path: row.path },
    execute_allowed: false,
    execute_blocking_reasons: ["Run Draft Publish dry-run before confirming."],
  }, "handoff");
  result.textContent = JSON.stringify({
    ok: true,
    source: "external_intake",
    path: row.path,
    message: "External intake draft path loaded. Run Draft Publish dry-run before confirming.",
  }, null, 2);
}

function prefillOwnerResolutionFromDecisionRecord() {
  const row = selectedOwnerDecisionRecord;
  const result = document.getElementById("owner-resolution-result");
  if (!row) {
    result.textContent = JSON.stringify({ ok: false, message: "Select an owner decision record first." }, null, 2);
    return;
  }
  const compatibleStatuses = new Set(["approved", "rejected", "needs_revision", "deferred"]);
  const decision = compatibleStatuses.has(row.decision_status) ? row.decision_status : "approved";
  setOwnerResolutionFields({
    "owner-resolution-request-id": row.decision_id || row.path || "",
    "owner-resolution-orchestration-id": row.orchestration_id || "",
    "owner-resolution-decision-type": row.decision_type || "owner_decision_record_review",
    "owner-resolution-related-task-id": row.task_id || "",
    "owner-resolution-related-iteration-id": "",
    "owner-resolution-forum-ref": row.external_ref || row.draft_path || row.path || "",
    "owner-resolution-evidence-ref": row.evidence_id ? `owner_decision_record:${row.evidence_id}` : (row.external_ref || row.path || ""),
    "owner-resolution-decision": decision,
    "owner-resolution-reason": [
      `Owner decision record ${valueOrDash(row.decision_id)} selected.`,
      `status=${valueOrDash(row.decision_status)}`,
      `type=${valueOrDash(row.decision_type)}`,
      `path=${valueOrDash(row.path)}`,
    ].join("\n"),
  });
  result.textContent = JSON.stringify({
    ok: true,
    source: "owner_decision_record",
    message: "Owner decision record copied into Owner Decision Resolution Review. Review before submitting.",
    decision_id: row.decision_id,
    path: row.path,
  }, null, 2);
}

function loadOwnerRecordFromResolution() {
  const requestId = document.getElementById("owner-resolution-request-id").value.trim();
  const decisionType = document.getElementById("owner-resolution-decision-type").value.trim();
  const decision = document.getElementById("owner-resolution-decision").value.trim();
  const relatedTaskId = document.getElementById("owner-resolution-related-task-id").value.trim();
  const evidenceRef = document.getElementById("owner-resolution-evidence-ref").value.trim();
  const forumRef = document.getElementById("owner-resolution-forum-ref").value.trim();
  const actor = document.getElementById("owner-resolution-actor").value.trim() || "owner";
  const reason = document.getElementById("owner-resolution-reason").value.trim();
  const normalizedStatus = decision === "deferred" || decision === "scope_reduced" ? "needs_revision" : decision;
  const inferredProject = selectedExternalIntake?.client_tag || selectedOwnerDecisionRecord?.client_tag || selectedOwnerDecisionRecord?.project || "";
  const inferredDraftPath = selectedExternalIntake?.path || selectedOwnerDecisionRecord?.draft_path || "";
  const inferredExternalRef = selectedExternalIntake?.external_ref || selectedOwnerDecisionRecord?.external_ref || forumRef || evidenceRef;
  setOwnerResolutionFields({
    "owner-record-decision-id": requestId || `decision-${Date.now()}`,
    "owner-record-decision-type": decisionType || "external_intake_review",
    "owner-record-decision-status": normalizedStatus || "approved",
    "owner-record-project": inferredProject,
    "owner-record-task-id": relatedTaskId || selectedExternalIntake?.task_id || selectedOwnerDecisionRecord?.task_id || "",
    "owner-record-draft-path": inferredDraftPath,
    "owner-record-external-ref": inferredExternalRef,
    "owner-record-evidence-id": evidenceRef || forumRef || requestId || "",
    "owner-record-source-tag": selectedExternalIntake?.source_tag || selectedOwnerDecisionRecord?.source_tag || "board",
    "owner-record-decided-by": actor,
    "owner-record-actor": actor,
    "owner-record-summary": reason || requestId || "Owner decision recorded from Board.",
    "owner-record-rationale": reason,
  });
  document.getElementById("owner-record-result").textContent = JSON.stringify({
    ok: true,
    message: "Resolution fields copied into Owner Decision Record. Run dry-run before recording.",
    request_id: requestId,
  }, null, 2);
}

function loadSelectedOwnerDecisionForResolution() {
  const row = selectedOwnerDecision;
  const result = document.getElementById("owner-resolution-result");
  if (!row) {
    result.textContent = JSON.stringify({ ok: false, message: "Select an Owner decision request first." }, null, 2);
    return;
  }
  document.getElementById("owner-resolution-request-id").value = row.request_id || "";
  document.getElementById("owner-resolution-orchestration-id").value = row.related_orchestration_id || "";
  document.getElementById("owner-resolution-decision-type").value = row.decision_type || "owner_review";
  document.getElementById("owner-resolution-related-task-id").value = row.related_task_id || "";
  document.getElementById("owner-resolution-related-iteration-id").value = row.related_iteration_id || "";
  const refs = [...(row.source_refs || []), ...(row.timeline_refs || [])].filter(Boolean);
  document.getElementById("owner-resolution-forum-ref").value = refs.find((ref) => String(ref).startsWith("forum://")) || refs[0] || "";
  document.getElementById("owner-resolution-evidence-ref").value = `owner_decision:${row.request_id || "selected"}`;
  document.getElementById("owner-resolution-reason").value = row.summary || "";
  result.textContent = JSON.stringify({ ok: true, message: "Selected Owner decision loaded for resolution review.", request_id: row.request_id }, null, 2);
}

function forumEventReviewPayload() {
  return {
    orchestration_id: document.getElementById("event-orchestration-id").value.trim(),
    event_type: document.getElementById("event-type").value.trim(),
    severity: document.getElementById("event-severity").value.trim(),
    actor: document.getElementById("event-actor").value.trim(),
    source: document.getElementById("event-source").value.trim(),
    forum_thread_ref: document.getElementById("event-forum-ref").value.trim(),
    related_task_id: document.getElementById("event-related-task-id").value.trim(),
    related_subtask_id: document.getElementById("event-related-subtask-id").value.trim(),
    related_iteration_id: document.getElementById("event-related-iteration-id").value.trim(),
    summary: document.getElementById("event-summary").value.trim(),
    details: document.getElementById("event-details").value.trim(),
    refs: document.getElementById("event-refs").value,
  };
}

function plannerLoopPersistencePayloadObject() {
  const raw = document.getElementById("planner-persist-payload").value.trim();
  if (!raw) {
    return { ok: false, message: "append payload JSON is required." };
  }
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
      return { ok: false, message: "append payload JSON must be an object." };
    }
    return parsed;
  } catch (err) {
    return { ok: false, message: `append payload JSON is invalid: ${String(err)}` };
  }
}

function plannerLoopPersistencePayload() {
  const payload = plannerLoopPersistencePayloadObject();
  if (payload?.ok === false) {
    return payload;
  }
  return {
    operation: document.getElementById("planner-persist-operation").value,
    actor: document.getElementById("planner-persist-actor").value.trim(),
    payload,
  };
}

function plannerPersistenceOrchestrationId(data) {
  const payload = data?.data?.original_payload || data?.data?.event_entry || data?.data?.iteration_entry || {};
  const target = data?.data?.target_path || data?.summary?.target_path || "";
  if (payload.orchestration_id) {
    return payload.orchestration_id;
  }
  const match = String(target).match(/^5_tasks\/orchestration\/([^/]+)\//);
  return match ? match[1] : "";
}

function summarizePlannerLoopPersistenceHandoff(data) {
  const orchestrationId = plannerPersistenceOrchestrationId(data);
  return {
    ok: data?.ok,
    verdict: data?.verdict,
    operation: data?.operation,
    dry_run: data?.dry_run,
    orchestration_id: orchestrationId,
    target_path: data?.data?.target_path || data?.summary?.target_path,
    write_snapshot_hash: data?.data?.write_snapshot_hash || data?.summary?.write_snapshot_hash,
    append_only: data?.data?.append_only,
    dry_run_id: data?.dry_run_id,
    execute_allowed: data?.execute_allowed,
    owner_confirmation_required: data?.owner_confirmation_required,
    performed_writes: data?.performed_writes || [],
    planned_writes: data?.planned_writes || [],
    next_refresh_targets: orchestrationId ? [
      "orchestration timeline",
      "orchestration summary",
      "context pack preview",
      "planner loop control desk",
    ] : [],
    warnings: data?.warnings || [],
    blocking_reasons: data?.blocking_reasons || [],
    errors: data?.errors || [],
  };
}

function renderPlannerLoopPersistenceHandoff(data) {
  const card = document.getElementById("planner-persist-handoff-card");
  card.replaceChildren();
  if (!data) {
    card.textContent = "Run a planner loop persistence dry-run to prepare handoff refresh.";
    document.getElementById("planner-persist-refresh-handoff").disabled = true;
    return;
  }
  const summary = summarizePlannerLoopPersistenceHandoff(data);
  const grid = document.createElement("div");
  grid.className = "planner-persist-grid";
  grid.append(
    createTextBlock("planner-persist-chip", "Status", summary.ok ? "ready" : "blocked"),
    createTextBlock("planner-persist-chip", "Operation", summary.operation),
    createTextBlock("planner-persist-chip", "Orchestration", summary.orchestration_id),
    createTextBlock("planner-persist-chip", "Target", summary.target_path),
    createTextBlock("planner-persist-chip", "Token", summary.dry_run_id ? "present" : "none"),
    createTextBlock("planner-persist-chip", "Execute", summary.execute_allowed ? "allowed" : "blocked")
  );
  card.append(
    grid,
    createListBlock("Performed Writes", summary.performed_writes),
    createListBlock("Planned Writes", summary.planned_writes),
    createListBlock("Next Refresh", summary.next_refresh_targets),
    createListBlock("Safety Blocks", summary.blocking_reasons)
  );
  document.getElementById("planner-persist-refresh-handoff").disabled = !summary.orchestration_id;
}


function summarizeOrchestrationSummary(data) {
  const preview = data?.data || {};
  const summary = preview.planned_summary || data?.summary || {};
  return {
    ok: data?.ok,
    verdict: data?.verdict,
    operation: data?.operation,
    dry_run: data?.dry_run,
    orchestration_id: summary.orchestration_id || preview.orchestration_id,
    parent_task_id: summary.parent_task_id,
    status: summary.status,
    current_iteration: summary.current_iteration,
    open_subtask_count: summary.open_subtask_count,
    completed_subtask_count: summary.completed_subtask_count,
    blocked_subtask_count: summary.blocked_subtask_count,
    failed_subtask_count: summary.failed_subtask_count,
    needs_owner: summary.needs_owner,
    needs_owner_reasons: summary.needs_owner_reasons || [],
    source_refs: preview.source_refs || [],
    conflicts: preview.conflicts || [],
    rebuild_notes: preview.rebuild_notes || [],
    writes_enabled: preview.writes_enabled,
    execute_allowed: data?.execute_allowed || preview.execute_allowed,
    dry_run_token: data?.dry_run_token || preview.dry_run_token,
    planned_writes: data?.planned_writes || [],
    warnings: data?.warnings || preview.warnings || [],
    blocking_reasons: data?.blocking_reasons || preview.blocking_reasons || [],
    owner_confirmation_required: data?.owner_confirmation_required || preview.owner_confirmation_required,
    owner_confirmation_reasons: data?.owner_confirmation_reasons || [],
  };
}

function createTextBlock(className, label, value) {
  const block = document.createElement("div");
  block.className = className;
  const title = document.createElement("strong");
  title.textContent = label;
  const body = document.createElement("span");
  body.textContent = valueOrDash(value);
  block.append(title, body);
  return block;
}

function createListBlock(label, values) {
  const block = document.createElement("div");
  block.className = "summary-list-block";
  const title = document.createElement("strong");
  title.textContent = label;
  block.appendChild(title);
  const list = document.createElement("ul");
  const rows = Array.isArray(values) ? values : [];
  if (rows.length === 0) {
    const item = document.createElement("li");
    item.textContent = "-";
    list.appendChild(item);
  }
  for (const value of rows) {
    const item = document.createElement("li");
    item.textContent = String(value);
    list.appendChild(item);
  }
  block.appendChild(list);
  return block;
}

function plannerDraftReviewRows(data) {
  const rows = data?.data?.drafts;
  return Array.isArray(rows) ? rows : [];
}

function plannerDraftReviewLabel(row) {
  return [row.task_id, row.title, row.path].filter(Boolean).join(" | ");
}

function renderPlannerDraftReviewDesk(data) {
  const list = document.getElementById("planner-drafts-review-list");
  if (!list) {
    return;
  }
  const rows = plannerDraftReviewRows(data);
  list.replaceChildren();
  const detail = document.getElementById("planner-drafts-review-detail");
  if (detail) {
    detail.textContent = rows.length ? "Select a planner draft to review readiness." : "No planner-created drafts found.";
  }
  if (rows.length === 0) {
    list.textContent = "No planner-created drafts found.";
    return;
  }
  for (const row of rows) {
    const card = document.createElement("article");
    card.className = `planner-draft-card status-${row.review_status || "review"}`;
    const header = document.createElement("div");
    header.className = "planner-draft-header";
    const title = document.createElement("strong");
    title.textContent = plannerDraftReviewLabel(row) || "planner draft";
    const status = document.createElement("span");
    status.textContent = row.review_status || "review";
    header.append(title, status);

    const meta = document.createElement("div");
    meta.className = "planner-draft-meta";
    meta.append(
      createTextBlock("planner-draft-chip", "Assigned", row.assigned_to),
      createTextBlock("planner-draft-chip", "Reviewer", row.reviewer),
      createTextBlock("planner-draft-chip", "Auditor", row.audit_by),
      createTextBlock("planner-draft-chip", "Depends", row.depends_on),
      createTextBlock("planner-draft-chip", "Planner", row.planner_agent),
      createTextBlock("planner-draft-chip", "Tier", row.planner_model_tier),
      createTextBlock("planner-draft-chip", "Publish", row.publish_status),
      createTextBlock("planner-draft-chip", "Target", row.target_path)
    );

    const gates = document.createElement("div");
    gates.className = "planner-draft-gates";
    gates.append(
      createListBlock("Missing Metadata", row.missing_metadata || []),
      createListBlock("Blocking Reasons", row.blocking_reasons || []),
      createListBlock("Warnings", row.warnings || [])
    );

    const actions = document.createElement("div");
    actions.className = "toolbar planner-draft-actions";
    const reviewButton = document.createElement("button");
    reviewButton.textContent = "Review";
    reviewButton.addEventListener("click", () => reviewPlannerDraftPath(row.path, { allowPublishHandoff: false }));
    const prepareButton = document.createElement("button");
    prepareButton.textContent = "Prepare Publish";
    prepareButton.disabled = !row.publish_ready || row.owner_gate;
    prepareButton.addEventListener("click", () => prepareApprovedPlannerDraftPublish(row.path));
    const disabledNote = document.createElement("span");
    disabledNote.className = "planner-draft-disabled-note";
    disabledNote.textContent = row.publish_ready && !row.owner_gate ? "controlled publish available" : "review only";
    actions.append(reviewButton, prepareButton, disabledNote);

    card.append(header, meta, gates, actions);
    list.appendChild(card);
  }
}

function renderPlannerDraftReviewDetail(data) {
  const detail = document.getElementById("planner-drafts-review-detail");
  if (!detail) {
    return;
  }
  detail.textContent = JSON.stringify(summarizePlannerDraftReview(data), null, 2);
}


function ownerDecisionRows(data) {
  const rows = data?.data?.decision_requests;
  return Array.isArray(rows) ? rows : [];
}

function renderOwnerDecisionDetail(row) {
  selectedOwnerDecision = row;
  const detail = document.getElementById("owner-decisions-detail");
  if (!detail) {
    return;
  }
  detail.textContent = JSON.stringify({
    request_id: row.request_id,
    source: row.source,
    decision_type: row.decision_type,
    title: row.title,
    summary: row.summary,
    severity: row.severity,
    status: row.status,
    related_task_id: row.related_task_id,
    related_orchestration_id: row.related_orchestration_id,
    related_iteration_id: row.related_iteration_id,
    source_refs: row.source_refs || [],
    timeline_refs: row.timeline_refs || [],
    owner_decision_required: row.owner_decision_required,
    review_only: row.review_only,
    resolution_enabled: row.resolution_enabled,
  }, null, 2);
}

function renderOwnerDecisionGate(data) {
  const list = document.getElementById("owner-decisions-list");
  if (!list) {
    return;
  }
  const rows = ownerDecisionRows(data);
  list.replaceChildren();
  const detail = document.getElementById("owner-decisions-detail");
  if (detail) {
    detail.textContent = rows.length ? "Select an Owner decision request." : "No open Owner decision requests found.";
  }
  if (rows.length === 0) {
    list.textContent = "No open Owner decision requests found.";
    return;
  }
  for (const row of rows) {
    const card = document.createElement("article");
    card.className = `owner-decision-card severity-${row.severity || "needs_owner"}`;
    const header = document.createElement("div");
    header.className = "owner-decision-header";
    const title = document.createElement("strong");
    title.textContent = row.title || row.request_id || "Owner decision request";
    const type = document.createElement("span");
    type.textContent = row.decision_type || "owner_review";
    header.append(title, type);

    const summary = document.createElement("p");
    summary.textContent = row.summary || "Owner decision requested.";

    const meta = document.createElement("div");
    meta.className = "owner-decision-meta";
    meta.append(
      createTextBlock("owner-decision-chip", "Source", row.source),
      createTextBlock("owner-decision-chip", "Severity", row.severity),
      createTextBlock("owner-decision-chip", "Task", row.related_task_id),
      createTextBlock("owner-decision-chip", "Orchestration", row.related_orchestration_id),
      createTextBlock("owner-decision-chip", "Iteration", row.related_iteration_id),
      createTextBlock("owner-decision-chip", "Status", row.status)
    );

    const refs = document.createElement("div");
    refs.className = "owner-decision-refs";
    refs.append(
      createListBlock("Source Refs", row.source_refs || []),
      createListBlock("Timeline Refs", row.timeline_refs || [])
    );

    const actions = document.createElement("div");
    actions.className = "toolbar owner-decision-actions";
    const reviewButton = document.createElement("button");
    reviewButton.textContent = "Review";
    reviewButton.addEventListener("click", () => renderOwnerDecisionDetail(row));
    const prepareButton = document.createElement("button");
    prepareButton.textContent = "Prepare Resolution";
    prepareButton.addEventListener("click", () => {
      renderOwnerDecisionDetail(row);
      loadSelectedOwnerDecisionForResolution();
    });
    const readOnly = document.createElement("span");
    readOnly.className = "owner-decision-readonly-note";
    readOnly.textContent = row.resolution_enabled ? "resolution available" : "review only";
    actions.append(reviewButton, prepareButton, readOnly);

    card.append(header, summary, meta, refs, actions);
    list.appendChild(card);
  }
}

function renderOrchestrationSummaryCard(data) {
  const card = document.getElementById("orchestration-summary-card");
  card.replaceChildren();
  if (!data) {
    card.textContent = "Enter an orchestration id and preview.";
    return;
  }
  const summary = summarizeOrchestrationSummary(data);
  const header = document.createElement("div");
  header.className = "summary-card-header";
  header.append(
    createTextBlock("summary-key", "Status", summary.status),
    createTextBlock("summary-key", "Iteration", summary.current_iteration),
    createTextBlock("summary-key", "Owner", summary.needs_owner ? "needs attention" : "clear")
  );

  const counts = document.createElement("div");
  counts.className = "summary-metrics";
  counts.append(
    createTextBlock("metric", "Open", summary.open_subtask_count),
    createTextBlock("metric", "Completed", summary.completed_subtask_count),
    createTextBlock("metric", "Blocked", summary.blocked_subtask_count),
    createTextBlock("metric", "Failed", summary.failed_subtask_count)
  );

  const refs = document.createElement("div");
  refs.className = "summary-grid";
  refs.append(
    createListBlock("Owner Attention", summary.needs_owner_reasons),
    createListBlock("Source Refs", summary.source_refs),
    createListBlock("Conflicts", summary.conflicts),
    createListBlock("Rebuild Notes", summary.rebuild_notes)
  );

  card.append(
    createTextBlock("summary-title", "Orchestration", summary.orchestration_id),
    createTextBlock("summary-title", "Parent Task", summary.parent_task_id),
    header,
    counts,
    refs
  );
}

function summarizeOrchestrationTimeline(data) {
  const preview = data?.data || {};
  const summary = preview.summary || data?.summary || {};
  const timeline = Array.isArray(preview.timeline) ? preview.timeline : [];
  return {
    ok: data?.ok,
    verdict: data?.verdict,
    operation: data?.operation,
    dry_run: data?.dry_run,
    orchestration_id: preview.orchestration_id || summary.orchestration_id,
    timeline_items: summary.timeline_items || timeline.length,
    planner_iterations: summary.planner_iterations,
    orchestration_events: summary.orchestration_events,
    owner_attention_count: summary.owner_attention_count,
    blocking_count: summary.blocking_count,
    first_event_at: summary.first_event_at,
    latest_event_at: summary.latest_event_at,
    source_refs: preview.source_refs || [],
    conflicts: preview.conflicts || [],
    timeline: timeline.map((item) => ({
      timestamp: item.timestamp,
      kind: item.kind,
      severity: item.severity,
      title: item.title,
      summary: item.summary,
      actor: item.actor,
      source_ref: item.source_ref,
      refs: item.refs || [],
      owner_attention_required: item.owner_attention_required,
      blocking: item.blocking,
    })),
    writes_enabled: preview.writes_enabled,
    execute_allowed: data?.execute_allowed || preview.execute_allowed,
    dry_run_token: data?.dry_run_token || preview.dry_run_token,
    planned_writes: data?.planned_writes || [],
    warnings: data?.warnings || preview.warnings || [],
    blocking_reasons: data?.blocking_reasons || preview.blocking_reasons || [],
    owner_confirmation_required: data?.owner_confirmation_required || preview.owner_confirmation_required,
    owner_confirmation_reasons: data?.owner_confirmation_reasons || [],
  };
}


function summarizePlannerLoopMvp(data) {
  const preview = data?.data || {};
  const step = preview.recommended_step || {};
  const loop = preview.loop_state_preview || {};
  const ownerGate = preview.owner_gate || {};
  return {
    ok: data?.ok,
    verdict: data?.verdict,
    operation: data?.operation,
    orchestration_id: preview.orchestration_id || data?.summary?.orchestration_id,
    recommended_step: step.step,
    recommended_route: step.route,
    reason: step.reason,
    owner_gate_active: ownerGate.active,
    owner_reasons: ownerGate.reasons || [],
    current_status: loop.status,
    current_iteration: loop.current_iteration,
    open_subtask_count: loop.open_subtask_count,
    timeline_items: loop.timeline_items,
    draft_candidates: (preview.draft_candidates || []).map((draft) => ({
      task_id: draft.task_id,
      path: draft.path,
      publish_status: draft.publish_status,
      publish_ready: draft.publish_ready,
      owner_gate: draft.owner_gate,
    })),
    controlled_handoffs: preview.controlled_handoffs || [],
    writes_enabled: preview.writes_enabled,
    controlled_mutation_enabled: preview.controlled_mutation_enabled,
    autonomous_runtime_enabled: preview.autonomous_runtime_enabled,
    automatic_polling_enabled: preview.automatic_polling_enabled,
    automatic_agent_execution_enabled: preview.automatic_agent_execution_enabled,
    automatic_publish_enabled: preview.automatic_publish_enabled,
    automatic_claim_enabled: preview.automatic_claim_enabled,
    automatic_push_enabled: preview.automatic_push_enabled,
    self_audit_enabled: preview.self_audit_enabled,
    execute_allowed: data?.execute_allowed || preview.execute_allowed,
    dry_run_token: data?.dry_run_token || preview.dry_run_token,
    planned_writes: data?.planned_writes || preview.planned_writes || [],
    warnings: data?.warnings || preview.warnings || [],
    blocking_reasons: data?.blocking_reasons || preview.blocking_reasons || [],
    execute_blocking_reasons: data?.execute_blocking_reasons || [],
  };
}

function summarizeContextPack(data) {
  const pack = data?.data || {};
  const task = pack.task || {};
  const bundle = pack.context_bundle || {};
  const orchestration = pack.orchestration || {};
  const governance = pack.governance || {};
  return {
    ok: data?.ok,
    verdict: data?.verdict,
    operation: data?.operation,
    pack_id: pack.pack_id || data?.summary?.pack_id,
    scope: pack.scope || data?.summary?.scope,
    source_type: pack.source_type || data?.summary?.source_type,
    task_id: task.task_id,
    task_path: task.path,
    title: task.title,
    context_bundle_ref: bundle.ref || task.context_bundle_ref,
    context_bundle_found: bundle.found,
    context_bundle_path: bundle.path,
    orchestration_id: orchestration.orchestration_id,
    owner_attention: orchestration.owner_attention,
    source_refs: pack.source_refs || [],
    disabled_capabilities: pack.disabled_capabilities || [],
    owner_decision_gates_preserved: governance.owner_decision_gates_preserved,
    self_audit_allowed: governance.self_audit_allowed,
    external_rag_allowed: governance.external_rag_allowed,
    cortex_replacement: governance.cortex_replacement,
    writes_enabled: pack.writes_enabled,
    execute_allowed: data?.execute_allowed || pack.execute_allowed,
    controlled_mutation_enabled: pack.controlled_mutation_enabled,
    external_rag_enabled: pack.external_rag_enabled,
    agent_execution_enabled: pack.agent_execution_enabled,
    git_automation_enabled: pack.git_automation_enabled,
    dry_run_token: data?.dry_run_token || pack.dry_run_token,
    planned_writes: data?.planned_writes || pack.planned_writes || [],
    planned_moves: data?.planned_moves || pack.planned_moves || [],
    warnings: data?.warnings || pack.warnings || [],
    blocking_reasons: data?.blocking_reasons || pack.blocking_reasons || [],
    needs_owner_reasons: data?.needs_owner_reasons || pack.needs_owner_reasons || [],
  };
}

function renderContextPackPreview(data) {
  const card = document.getElementById("context-pack-card");
  card.replaceChildren();
  if (!data) {
    card.textContent = "Select a task or enter a source to preview context.";
    return;
  }
  const summary = summarizeContextPack(data);
  const safety = document.createElement("div");
  safety.className = "context-pack-grid";
  safety.append(
    createTextBlock("context-pack-chip", "Writes", summary.writes_enabled ? "enabled" : "disabled"),
    createTextBlock("context-pack-chip", "Execute", summary.execute_allowed ? "enabled" : "disabled"),
    createTextBlock("context-pack-chip", "External RAG", summary.external_rag_enabled ? "enabled" : "disabled"),
    createTextBlock("context-pack-chip", "Agent Run", summary.agent_execution_enabled ? "enabled" : "disabled"),
    createTextBlock("context-pack-chip", "Git", summary.git_automation_enabled ? "enabled" : "disabled"),
    createTextBlock("context-pack-chip", "Token", summary.dry_run_token || "none")
  );
  const identity = document.createElement("div");
  identity.className = "context-pack-grid";
  identity.append(
    createTextBlock("context-pack-chip", "Scope", summary.scope),
    createTextBlock("context-pack-chip", "Source", summary.source_type),
    createTextBlock("context-pack-chip", "Task", summary.task_id || summary.task_path),
    createTextBlock("context-pack-chip", "Bundle", summary.context_bundle_ref),
    createTextBlock("context-pack-chip", "Bundle Found", summary.context_bundle_found),
    createTextBlock("context-pack-chip", "Orchestration", summary.orchestration_id)
  );
  card.append(
    createTextBlock("summary-title", "Pack", summary.pack_id),
    createTextBlock("summary-title", "Title", summary.title),
    identity,
    safety,
    createListBlock("Source Refs", summary.source_refs),
    createListBlock("Needs Owner", summary.needs_owner_reasons),
    createListBlock("Warnings", summary.warnings),
    createListBlock("Disabled", summary.disabled_capabilities)
  );
}

function renderPlannerLoopMvp(data) {
  const card = document.getElementById("planner-loop-card");
  card.replaceChildren();
  if (!data) {
    card.textContent = "Enter an orchestration id and preview the next safe step.";
    return;
  }
  const summary = summarizePlannerLoopMvp(data);
  const header = document.createElement("div");
  header.className = "planner-loop-header";
  header.append(
    createTextBlock("planner-loop-chip", "Step", summary.recommended_step),
    createTextBlock("planner-loop-chip", "Route", summary.recommended_route),
    createTextBlock("planner-loop-chip", "Owner", summary.owner_gate_active ? "needs decision" : "clear"),
    createTextBlock("planner-loop-chip", "Writes", summary.writes_enabled ? "enabled" : "disabled")
  );
  const body = document.createElement("div");
  body.className = "planner-loop-grid";
  body.append(
    createTextBlock("planner-loop-chip", "Status", summary.current_status),
    createTextBlock("planner-loop-chip", "Iteration", summary.current_iteration),
    createTextBlock("planner-loop-chip", "Open", summary.open_subtask_count),
    createTextBlock("planner-loop-chip", "Timeline", summary.timeline_items),
    createListBlock("Owner Reasons", summary.owner_reasons),
    createListBlock("Draft Candidates", summary.draft_candidates.map((draft) => `${draft.task_id || draft.path}: ${draft.publish_status || "draft"}`)),
    createListBlock("Controlled Handoffs", summary.controlled_handoffs.map((handoff) => `${handoff.route}: ${handoff.available ? "available" : "blocked"}`)),
    createListBlock("Safety Blocks", summary.execute_blocking_reasons)
  );
  card.append(createTextBlock("summary-title", "Reason", summary.reason), header, body);
}

function renderOrchestrationTimeline(data) {
  const list = document.getElementById("orchestration-timeline-list");
  list.replaceChildren();
  if (!data) {
    list.textContent = "Enter an orchestration id and load timeline.";
    return;
  }
  const preview = data?.data || {};
  const rows = Array.isArray(preview.timeline) ? preview.timeline : [];
  if (rows.length === 0) {
    list.textContent = "No timeline entries found.";
    return;
  }
  for (const row of rows) {
    const item = document.createElement("article");
    item.className = `timeline-item severity-${row.severity || "info"}`;
    const header = document.createElement("div");
    header.className = "timeline-item-header";
    const title = document.createElement("strong");
    title.textContent = row.title || row.id || row.kind || "timeline item";
    const time = document.createElement("span");
    time.textContent = row.timestamp || "-";
    header.append(title, time);
    const summary = document.createElement("p");
    summary.textContent = row.summary || "-";
    const meta = document.createElement("div");
    meta.className = "timeline-meta";
    meta.append(
      createTextBlock("timeline-chip", "Kind", row.kind),
      createTextBlock("timeline-chip", "Severity", row.severity),
      createTextBlock("timeline-chip", "Actor", row.actor),
      createTextBlock("timeline-chip", "Source", row.source_ref)
    );
    const refs = createListBlock("Refs", row.refs || []);
    refs.classList.add("timeline-refs");
    item.append(header, summary, meta, refs);
    list.appendChild(item);
  }
}

function summarizePlannerDraftReview(data) {
  const review = data?.data || {};
  const publishPreview = review.publish_preview || {};
  const handoff = review.handoff_to_draft_publish || {};
  return {
    ok: data?.ok,
    verdict: data?.verdict,
    operation: data?.operation,
    task_id: review.task_id,
    path: review.path,
    planner_created: review.planner_created,
    publish_eligible: data?.summary?.publish_eligible,
    preconditions_total: data?.summary?.preconditions_total,
    preconditions_passed: data?.summary?.preconditions_passed,
    preconditions: review.preconditions || [],
    publish_preview_verdict: publishPreview.verdict,
    source_path: publishPreview.source_path,
    target_path: publishPreview.target_path,
    would_write_when_published: publishPreview.would_write,
    publish_preview_planned_writes: publishPreview.planned_writes || [],
    handoff_to_draft_publish_enabled: handoff.enabled,
    handoff_path: handoff.path,
    writes_enabled: review.writes_enabled,
    review_only: review.review_only,
    controlled_execute_expanded: review.controlled_execute_expanded,
    planned_writes: data?.planned_writes || [],
    warnings: data?.warnings || [],
    blocking_reasons: data?.blocking_reasons || [],
    needs_owner_reasons: data?.needs_owner_reasons || [],
    execute_allowed: data?.execute_allowed,
    execute_blocking_reasons: data?.execute_blocking_reasons || [],
    errors: data?.errors || [],
  };
}

function summarizeOwnerDecisionResolution(data) {
  const review = data?.data || {};
  const request = review.resolution_request || {};
  const appendPlan = review.append_plan || {};
  return {
    ok: data?.ok,
    verdict: data?.verdict,
    operation: data?.operation,
    request_id: request.request_id || data?.summary?.request_id,
    decision: request.decision || data?.summary?.decision,
    decision_type: request.decision_type || data?.summary?.decision_type,
    evidence_ref: request.evidence_ref,
    target_path: appendPlan.target_path,
    writer_review_passed: data?.summary?.writer_review_passed,
    resolution_review_only: review.resolution_review_only,
    decision_persistence_enabled: review.decision_persistence_enabled,
    controlled_mutation_allowed: review.controlled_mutation_allowed,
    writes_enabled: review.writes_enabled,
    planned_writes: data?.planned_writes || [],
    append_plan_planned_writes: appendPlan.planned_writes || [],
    execute_allowed: data?.execute_allowed,
    dry_run_token: data?.dry_run_token,
    blocking_reasons: data?.blocking_reasons || [],
    warnings: data?.warnings || [],
    execute_blocking_reasons: data?.execute_blocking_reasons || [],
  };
}

function renderOwnerDecisionResolution(data) {
  const card = document.getElementById("owner-resolution-card");
  card.replaceChildren();
  if (!data) {
    card.textContent = "Select an Owner decision request or fill the form.";
    return;
  }
  const summary = summarizeOwnerDecisionResolution(data);
  const header = document.createElement("div");
  header.className = "owner-resolution-grid";
  header.append(
    createTextBlock("owner-resolution-chip", "Decision", summary.decision),
    createTextBlock("owner-resolution-chip", "Type", summary.decision_type),
    createTextBlock("owner-resolution-chip", "Writes", summary.writes_enabled ? "enabled" : "disabled"),
    createTextBlock("owner-resolution-chip", "Execute", summary.execute_allowed ? "enabled" : "disabled")
  );
  card.append(
    createTextBlock("summary-title", "Request", summary.request_id),
    createTextBlock("summary-title", "Evidence", summary.evidence_ref),
    header,
    createListBlock("Blocking", summary.blocking_reasons),
    createListBlock("Safety", summary.execute_blocking_reasons)
  );
}

function summarizeForumEventReview(data) {
  const review = data?.data || {};
  const entry = review.event_entry || {};
  const appendPlan = review.append_plan || {};
  const handoff = review.handoff_to_future_writer || {};
  return {
    ok: data?.ok,
    verdict: data?.verdict,
    operation: data?.operation,
    event_id: entry.event_id,
    orchestration_id: entry.orchestration_id,
    event_type: entry.event_type,
    severity: entry.severity,
    target_path: appendPlan.target_path,
    append_only: appendPlan.append_only,
    writer_review_only: review.writer_review_only,
    writes_enabled: review.writes_enabled,
    forum_backend_enabled: review.forum_backend_enabled,
    network_posting_enabled: review.network_posting_enabled,
    controlled_execute_expanded: review.controlled_execute_expanded,
    future_writer_handoff_enabled: handoff.enabled,
    preconditions_total: data?.summary?.preconditions_total,
    preconditions_passed: data?.summary?.preconditions_passed,
    preconditions: review.preconditions || [],
    append_plan_planned_writes: appendPlan.planned_writes || [],
    planned_writes: data?.planned_writes || [],
    warnings: data?.warnings || [],
    blocking_reasons: data?.blocking_reasons || [],
    needs_owner_reasons: data?.needs_owner_reasons || [],
    execute_allowed: data?.execute_allowed,
    execute_blocking_reasons: data?.execute_blocking_reasons || [],
    errors: data?.errors || [],
  };
}

function summarizeParentRequirement(data) {
  const requirement = data?.data?.parent_requirement || {};
  const loop = data?.data?.planner_loop_preview || {};
  return {
    ok: data?.ok,
    verdict: data?.verdict,
    operation: data?.operation,
    requirement_id: requirement.requirement_id,
    title: requirement.title,
    project: requirement.project,
    intake_status: requirement.intake_status,
    forum_thread_ref: requirement.forum_thread_ref,
    assigned_planner: requirement.assigned_planner,
    assigned_planner_instance: requirement.assigned_planner_instance,
    planner_runtime_profile: requirement.planner_runtime_profile,
    min_planner_model_tier: requirement.min_planner_model_tier,
    planner_assignment_status: requirement.planner_assignment_status,
    orchestration_id: requirement.orchestration_id,
    parent_task_id: requirement.parent_task_id,
    next_expected_action: loop.next_expected_action,
    stop_conditions: loop.stop_conditions || [],
    owner_decision_required_for: loop.owner_decision_required_for || [],
    writes_enabled: data?.data?.writes_enabled,
    forum_backend_enabled: data?.data?.forum_backend_enabled,
    planner_runtime_launch_enabled: data?.data?.planner_runtime_launch_enabled,
    planned_writes: data?.planned_writes || [],
    warnings: data?.warnings || [],
    blocking_reasons: data?.blocking_reasons || [],
    execute_allowed: data?.execute_allowed,
    execute_blocking_reasons: data?.execute_blocking_reasons || [],
    errors: data?.errors || [],
  };
}


function summarizeManualPlannerTick(data) {
  const report = data?.data?.visible_report || {};
  const orchestrationSnapshot = data?.data?.orchestration_summary_snapshot || {};
  const timelineSnapshot = data?.data?.timeline_snapshot || {};
  const ownerSnapshot = data?.data?.owner_decision_snapshot || {};
  return {
    ok: data?.ok,
    verdict: data?.verdict,
    operation: data?.operation,
    orchestration_id: data?.summary?.orchestration_id,
    planner_iteration_id: data?.summary?.planner_iteration_id,
    decision: data?.summary?.decision,
    owner_decision_required: data?.summary?.owner_decision_required,
    critical_fork_hits: data?.summary?.critical_fork_hits || [],
    related_owner_decision_requests: data?.summary?.related_owner_decision_requests,
    manual_flow_next_step: report.manual_flow_next_step,
    inputs_read: report.inputs_read || [],
    observations: report.observations || [],
    publish_candidates: report.publish_candidates || [],
    audit_handoff_needed: report.audit_handoff_needed,
    next_expected_action: report.next_expected_action,
    orchestration_status: orchestrationSnapshot.status,
    timeline_event_count: timelineSnapshot.event_count || 0,
    owner_decision_total: ownerSnapshot.total || 0,
    related_owner_decision_details: ownerSnapshot.related_requests || [],
    writes_enabled: data?.data?.writes_enabled,
    planner_iteration_append_enabled: data?.data?.planner_iteration_append_enabled,
    forum_event_append_enabled: data?.data?.forum_event_append_enabled,
    planner_runtime_launch_enabled: data?.data?.planner_runtime_launch_enabled,
    queue_mutation_enabled: data?.data?.queue_mutation_enabled,
    planned_writes: data?.planned_writes || [],
    needs_owner_reasons: data?.needs_owner_reasons || [],
    execute_allowed: data?.execute_allowed,
    execute_blocking_reasons: data?.execute_blocking_reasons || [],
    warnings: data?.warnings || [],
  };
}

function renderManualPlannerTickCard(data) {
  const card = document.getElementById("manual-planner-tick-card");
  if (!card) {
    return;
  }
  card.replaceChildren();
  if (!data) {
    card.textContent = "Fill the planner tick fields and preview manual flow.";
    return;
  }
  const summary = summarizeManualPlannerTick(data);
  const header = document.createElement("div");
  header.className = "manual-tick-header";
  header.append(
    createTextBlock("manual-tick-chip", "Decision", summary.decision),
    createTextBlock("manual-tick-chip", "Next", summary.manual_flow_next_step || summary.next_expected_action),
    createTextBlock("manual-tick-chip", "Owner", summary.owner_decision_required ? "needs decision" : "clear"),
    createTextBlock("manual-tick-chip", "Audit", summary.audit_handoff_needed ? "handoff" : "none"),
    createTextBlock("manual-tick-chip", "Timeline", summary.timeline_event_count),
    createTextBlock("manual-tick-chip", "Writes", summary.writes_enabled ? "enabled" : "disabled")
  );
  const details = document.createElement("div");
  details.className = "manual-tick-grid";
  details.append(
    createListBlock("Inputs Read", summary.inputs_read),
    createListBlock("Observations", summary.observations),
    createListBlock("Publish Candidates", summary.publish_candidates),
    createListBlock("Critical Forks", summary.critical_fork_hits),
    createListBlock("Needs Owner", summary.needs_owner_reasons),
    createListBlock("Owner Requests", summary.related_owner_decision_details.map((request) => request.summary || request.title || request.request_id)),
    createListBlock("Execute Blocks", summary.execute_blocking_reasons)
  );
  card.append(header, details);
}

function summarizePlannerTick(data) {
  const iteration = data?.data?.planner_iteration || {};
  const report = data?.data?.visible_report || {};
  const events = Array.isArray(data?.data?.event_log_preview) ? data.data.event_log_preview : [];
  return {
    ok: data?.ok,
    verdict: data?.verdict,
    operation: data?.operation,
    planner_iteration_id: iteration.iteration_id,
    orchestration_id: iteration.orchestration_id,
    parent_task_id: report.parent_task_id,
    forum_thread_ref: report.forum_thread_ref,
    iteration_number: iteration.iteration_number,
    planner_agent: iteration.planner_agent,
    planner_agent_instance: iteration.planner_agent_instance,
    planner_model_tier: iteration.planner_model_tier,
    combined_planner_executor: report.combined_planner_executor,
    decision: report.decision,
    decision_reason: report.decision_reason,
    owner_decision_required: report.owner_decision_required,
    needs_owner_reasons: report.needs_owner_reasons || [],
    inputs_read: report.inputs_read || [],
    observations: report.observations || [],
    subtask_drafts_proposed: report.subtask_drafts_proposed || [],
    publish_candidates: report.publish_candidates || [],
    repair_recommendations: report.repair_recommendations || [],
    audit_handoff_needed: report.audit_handoff_needed,
    next_expected_action: report.next_expected_action,
    stop_condition_hits: report.stop_condition_hits || [],
    event_count: events.length,
    event_types: events.map((event) => event.event_type),
    writes_enabled: data?.data?.writes_enabled,
    forum_backend_enabled: data?.data?.forum_backend_enabled,
    planner_runtime_launch_enabled: data?.data?.planner_runtime_launch_enabled,
    orchestration_writer_enabled: data?.data?.orchestration_writer_enabled,
    planned_writes: data?.planned_writes || [],
    warnings: data?.warnings || [],
    blocking_reasons: data?.blocking_reasons || [],
    execute_allowed: data?.execute_allowed,
    execute_blocking_reasons: data?.execute_blocking_reasons || [],
    errors: data?.errors || [],
  };
}


async function loadOrchestrationSummary() {
  const input = document.getElementById("orchestration-summary-id");
  const orchestrationId = input.value.trim();
  const el = document.getElementById("orchestration-summary");
  if (!orchestrationId) {
    const payload = { ok: false, message: "orchestration_id is required." };
    el.textContent = JSON.stringify(payload, null, 2);
    renderOrchestrationSummaryCard(null);
    return null;
  }
  latestOrchestrationSummary = null;
  setPanelLoading("orchestration-summary");
  renderOrchestrationSummaryCard(null);
  try {
    const response = await fetch(`/api/orchestration-summary?orchestration_id=${encodeURIComponent(orchestrationId)}`, { cache: "no-store" });
    const data = await response.json();
    latestOrchestrationSummary = data;
    latestDebug["/api/orchestration-summary"] = data;
    el.textContent = JSON.stringify(summarizeOrchestrationSummary(data), null, 2);
    renderOrchestrationSummaryCard(data);
    updateDebugPanel();
    return data;
  } catch (err) {
    setPanelError("orchestration-summary", err);
    renderOrchestrationSummaryCard(null);
    return { ok: false, error: String(err) };
  }
}

async function loadOrchestrationTimeline() {
  const input = document.getElementById("orchestration-timeline-id");
  const orchestrationId = input.value.trim();
  const el = document.getElementById("orchestration-timeline");
  if (!orchestrationId) {
    const payload = { ok: false, message: "orchestration_id is required." };
    el.textContent = JSON.stringify(payload, null, 2);
    renderOrchestrationTimeline(null);
    return null;
  }
  latestOrchestrationTimeline = null;
  setPanelLoading("orchestration-timeline");
  renderOrchestrationTimeline(null);
  try {
    const response = await fetch(`/api/orchestration-timeline?orchestration_id=${encodeURIComponent(orchestrationId)}`, { cache: "no-store" });
    const data = await response.json();
    latestOrchestrationTimeline = data;
    latestDebug["/api/orchestration-timeline"] = data;
    el.textContent = JSON.stringify(summarizeOrchestrationTimeline(data), null, 2);
    renderOrchestrationTimeline(data);
    updateDebugPanel();
    return data;
  } catch (err) {
    setPanelError("orchestration-timeline", err);
    renderOrchestrationTimeline(null);
    return { ok: false, error: String(err) };
  }
}


async function loadPlannerLoopMvp() {
  const orchestrationId = document.getElementById("planner-loop-id").value.trim();
  const actor = document.getElementById("planner-loop-actor").value.trim();
  const el = document.getElementById("planner-loop-result");
  if (!orchestrationId) {
    const payload = { ok: false, message: "orchestration_id is required." };
    el.textContent = JSON.stringify(payload, null, 2);
    renderPlannerLoopMvp(null);
    return null;
  }
  latestPlannerLoopPreview = null;
  setPanelLoading("planner-loop-result");
  renderPlannerLoopMvp(null);
  try {
    const params = new URLSearchParams({ orchestration_id: orchestrationId });
    if (actor) {
      params.set("actor", actor);
    }
    const response = await fetch(`/api/planner-loop/mvp?${params.toString()}`, { cache: "no-store" });
    const data = await response.json();
    latestPlannerLoopPreview = data;
    latestDebug["/api/planner-loop/mvp"] = data;
    el.textContent = JSON.stringify(summarizePlannerLoopMvp(data), null, 2);
    renderPlannerLoopMvp(data);
    updateDebugPanel();
    return data;
  } catch (err) {
    setPanelError("planner-loop-result", err);
    renderPlannerLoopMvp(null);
    return { ok: false, error: String(err) };
  }
}

function contextPackQuery() {
  const source = document.getElementById("context-pack-source").value;
  const selector = document.getElementById("context-pack-selector").value.trim();
  if (!selector) {
    return "";
  }
  return `${source}=${encodeURIComponent(selector)}`;
}

function loadSelectedTaskForContextPack() {
  const selector = selectedTask?.task_id || selectedTask?.path || document.getElementById("task-selector").value.trim();
  const source = selector.includes("/") ? "path" : "task_id";
  document.getElementById("context-pack-source").value = source;
  document.getElementById("context-pack-selector").value = selector;
  document.getElementById("context-pack-result").textContent = JSON.stringify({
    ok: Boolean(selector),
    selector: selector || null,
    message: selector ? "Selected task loaded. Preview context when ready." : "Select a task first.",
  }, null, 2);
}

async function loadContextPackPreview() {
  const query = contextPackQuery();
  const el = document.getElementById("context-pack-result");
  if (!query) {
    const payload = { ok: false, message: "context pack source is required." };
    el.textContent = JSON.stringify(payload, null, 2);
    renderContextPackPreview(null);
    return null;
  }
  latestContextPackPreview = null;
  setPanelLoading("context-pack-result");
  renderContextPackPreview(null);
  try {
    const response = await fetch(`/api/context-pack/preview?${query}`, { cache: "no-store" });
    const data = await response.json();
    latestContextPackPreview = data;
    latestDebug["/api/context-pack/preview"] = data;
    el.textContent = JSON.stringify(summarizeContextPack(data), null, 2);
    renderContextPackPreview(data);
    updateDebugPanel();
    return data;
  } catch (err) {
    setPanelError("context-pack-result", err);
    renderContextPackPreview(null);
    return { ok: false, error: String(err) };
  }
}

async function loadTaskDetail() {
  const query = taskQuery();
  const el = document.getElementById("task-detail");
  if (!query) {
    el.textContent = JSON.stringify({ ok: false, message: "Select a task first." }, null, 2);
    return null;
  }
  setPanelLoading("task-detail");
  try {
    const response = await fetch(`/api/task?${query}`, { cache: "no-store" });
    const data = await response.json();
    latestDebug["/api/task"] = data;
    el.textContent = JSON.stringify(summarizeTaskDetail(data), null, 2);
    updateDebugPanel();
    return data;
  } catch (err) {
    setPanelError("task-detail", err);
    return { ok: false, error: String(err) };
  }
}

async function loadTaskPreview() {
  const query = taskQuery();
  const actor = document.getElementById("preview-actor").value.trim();
  const el = document.getElementById("task-preview");
  if (!query || !actor) {
    el.textContent = JSON.stringify({ ok: false, message: "Task and actor are required." }, null, 2);
    return null;
  }
  localStorage.setItem(ACTOR_STORAGE_KEY, actor);
  setPanelLoading("task-preview");
  try {
    const response = await fetch(`/api/preview?${query}&actor=${encodeURIComponent(actor)}`, { cache: "no-store" });
    const data = await response.json();
    latestDebug["/api/preview"] = data;
    el.textContent = JSON.stringify(summarizePreview(data), null, 2);
    updateDebugPanel();
    return data;
  } catch (err) {
    setPanelError("task-preview", err);
    return { ok: false, error: String(err) };
  }
}

function executePayloadBase() {
  const query = taskQuery();
  const actor = document.getElementById("preview-actor").value.trim();
  if (!query || !actor) {
    return { ok: false, message: "Task and actor are required." };
  }
  const payload = { operation: "queue_claim", actor };
  const raw = document.getElementById("task-selector").value.trim();
  if (raw.includes("/")) {
    payload.path = raw;
  } else {
    payload.task_id = raw;
  }
  return payload;
}

async function runExecuteDryRun() {
  const el = document.getElementById("execute-result");
  const payload = executePayloadBase();
  if (payload.ok === false) {
    el.textContent = JSON.stringify(payload, null, 2);
    return null;
  }
  document.getElementById("execute-confirm").disabled = true;
  latestExecuteDryRun = null;
  setPanelLoading("execute-result");
  try {
    const response = await fetch("/api/execute/dry-run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    latestExecuteDryRun = data;
    latestDebug["/api/execute/dry-run"] = data;
    const canExecute = Boolean(data?.dry_run_id && data?.execute_allowed && data?.verdict !== "BLOCK");
    document.getElementById("execute-confirm").disabled = !canExecute;
    el.textContent = JSON.stringify(summarizeExecute(data), null, 2);
    updateDebugPanel();
    return data;
  } catch (err) {
    setPanelError("execute-result", err);
    return { ok: false, error: String(err) };
  }
}

async function confirmExecute() {
  const el = document.getElementById("execute-result");
  const actor = document.getElementById("preview-actor").value.trim();
  if (!latestExecuteDryRun?.dry_run_id || !actor) {
    el.textContent = JSON.stringify({ ok: false, message: "Dry-run token and actor are required." }, null, 2);
    return null;
  }
  const payload = {
    dry_run_id: latestExecuteDryRun.dry_run_id,
    actor,
    owner_confirmed: document.getElementById("owner-confirmed").checked,
  };
  document.getElementById("execute-confirm").disabled = true;
  setPanelLoading("execute-result");
  try {
    const response = await fetch("/api/execute/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    latestDebug["/api/execute/confirm"] = data;
    el.textContent = JSON.stringify(summarizeExecute(data), null, 2);
    updateDebugPanel();
    await refreshPanel("queue");
    await refreshPanel("validate");
    return data;
  } catch (err) {
    setPanelError("execute-result", err);
    return { ok: false, error: String(err) };
  }
}

async function runDraftDryRun() {
  const el = document.getElementById("draft-create-result");
  const payload = draftCreatePayload();
  if (!payload.actor || !payload.payload.frontmatter.task_id || !payload.payload.frontmatter.title) {
    el.textContent = JSON.stringify({ ok: false, message: "actor, task_id, and title are required." }, null, 2);
    return null;
  }
  document.getElementById("draft-create-confirm").disabled = true;
  latestDraftDryRun = null;
  setPanelLoading("draft-create-result");
  try {
    const response = await fetch("/api/execute/dry-run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    latestDraftDryRun = data;
    latestDebug["/api/execute/dry-run:draft_create"] = data;
    const canExecute = Boolean(data?.dry_run_id && data?.execute_allowed && data?.verdict !== "BLOCK");
    document.getElementById("draft-create-confirm").disabled = !canExecute;
    el.textContent = JSON.stringify(summarizeExecute(data), null, 2);
    updateDebugPanel();
    return data;
  } catch (err) {
    setPanelError("draft-create-result", err);
    return { ok: false, error: String(err) };
  }
}

async function confirmDraftCreate() {
  const el = document.getElementById("draft-create-result");
  const actor = document.getElementById("preview-actor").value.trim();
  if (!latestDraftDryRun?.dry_run_id || !actor) {
    el.textContent = JSON.stringify({ ok: false, message: "Draft dry-run token and actor are required." }, null, 2);
    return null;
  }
  document.getElementById("draft-create-confirm").disabled = true;
  setPanelLoading("draft-create-result");
  try {
    const response = await fetch("/api/execute/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        dry_run_id: latestDraftDryRun.dry_run_id,
        actor,
        owner_confirmed: document.getElementById("owner-confirmed").checked,
      }),
    });
    const data = await response.json();
    latestDebug["/api/execute/confirm:draft_create"] = data;
    el.textContent = JSON.stringify(summarizeExecute(data), null, 2);
    updateDebugPanel();
    await refreshPanel("drafts");
    return data;
  } catch (err) {
    setPanelError("draft-create-result", err);
    return { ok: false, error: String(err) };
  }
}

async function runDraftPublishDryRun() {
  const el = document.getElementById("draft-publish-result");
  const payload = draftPublishPayload();
  if (!payload.actor || !payload.path) {
    renderDraftPublishCard({
      ok: false,
      verdict: "BLOCK",
      operation: "draft_publish",
      data: { source_path: payload.path },
      execute_allowed: false,
      execute_blocking_reasons: ["actor and draft path are required."],
    }, "dry_run");
    el.textContent = JSON.stringify({ ok: false, message: "actor and draft path are required." }, null, 2);
    return null;
  }
  document.getElementById("draft-publish-confirm").disabled = true;
  latestDraftPublishDryRun = null;
  setPanelLoading("draft-publish-result");
  renderDraftPublishCard({
    ok: true,
    verdict: "RUNNING",
    operation: "draft_publish",
    data: { source_path: payload.path },
    execute_allowed: false,
  }, "dry_run");
  try {
    const response = await fetch("/api/execute/dry-run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    latestDraftPublishDryRun = data;
    latestDebug["/api/execute/dry-run:draft_publish"] = data;
    const canExecute = Boolean(data?.dry_run_id && data?.execute_allowed && data?.verdict !== "BLOCK");
    document.getElementById("draft-publish-confirm").disabled = !canExecute;
    renderDraftPublishCard(data, "dry_run");
    el.textContent = JSON.stringify(summarizeExecute(data), null, 2);
    updateDebugPanel();
    return data;
  } catch (err) {
    setPanelError("draft-publish-result", err);
    renderDraftPublishCard({
      ok: false,
      verdict: "ERROR",
      operation: "draft_publish",
      errors: [String(err)],
    }, "dry_run");
    return { ok: false, error: String(err) };
  }
}

async function confirmDraftPublish() {
  const el = document.getElementById("draft-publish-result");
  const actor = document.getElementById("preview-actor").value.trim();
  if (!latestDraftPublishDryRun?.dry_run_id || !actor) {
    renderDraftPublishCard({
      ok: false,
      verdict: "BLOCK",
      operation: "draft_publish",
      dry_run_id: latestDraftPublishDryRun?.dry_run_id,
      execute_allowed: false,
      execute_blocking_reasons: ["Draft publish dry-run token and actor are required."],
    }, "confirm");
    el.textContent = JSON.stringify({ ok: false, message: "Draft publish dry-run token and actor are required." }, null, 2);
    return null;
  }
  document.getElementById("draft-publish-confirm").disabled = true;
  setPanelLoading("draft-publish-result");
  renderDraftPublishCard({
    ok: true,
    verdict: "CONFIRMING",
    operation: "draft_publish",
    dry_run_id: latestDraftPublishDryRun.dry_run_id,
    execute_allowed: false,
  }, "confirm");
  try {
    const response = await fetch("/api/execute/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        dry_run_id: latestDraftPublishDryRun.dry_run_id,
        actor,
        owner_confirmed: document.getElementById("owner-confirmed").checked,
      }),
    });
    const data = await response.json();
    latestDebug["/api/execute/confirm:draft_publish"] = data;
    renderDraftPublishCard(data, "confirm");
    el.textContent = JSON.stringify(summarizeExecute(data), null, 2);
    updateDebugPanel();
    await refreshPanel("drafts");
    await refreshPanel("queue");
    await refreshPanel("validate");
    return data;
  } catch (err) {
    setPanelError("draft-publish-result", err);
    renderDraftPublishCard({
      ok: false,
      verdict: "ERROR",
      operation: "draft_publish",
      errors: [String(err)],
    }, "confirm");
    return { ok: false, error: String(err) };
  }
}


function updateApprovedPlannerDraftConfirmState() {
  const checkbox = document.getElementById("approved-planner-draft-confirmed");
  const button = document.getElementById("approved-planner-draft-confirm");
  if (!checkbox || !button) {
    return;
  }
  const canExecute = Boolean(
    latestApprovedPlannerDraftDryRun?.dry_run_id
    && latestApprovedPlannerDraftDryRun?.execute_allowed
    && latestApprovedPlannerDraftDryRun?.verdict !== "BLOCK"
    && latestApprovedPlannerDraftDryRun?.data?.owner_decision_gate?.clear
    && checkbox.checked
  );
  button.disabled = !canExecute;
}

function prepareApprovedPlannerDraftPublish(path) {
  document.getElementById("approved-planner-draft-path").value = path || "";
  document.getElementById("approved-planner-draft-confirmed").checked = false;
  latestApprovedPlannerDraftDryRun = null;
  document.getElementById("approved-planner-draft-confirm").disabled = true;
  document.getElementById("approved-planner-draft-result").textContent = JSON.stringify({
    ok: true,
    path,
    message: "Run approved planner draft dry-run before confirming.",
  }, null, 2);
}

function summarizeApprovedPlannerDraftPublish(data) {
  return {
    ok: data?.ok,
    verdict: data?.verdict,
    operation: data?.operation,
    actor: data?.actor?.actor,
    dry_run_id: data?.dry_run_id,
    dry_run_snapshot_hash: data?.dry_run_snapshot_hash,
    execute_allowed: data?.execute_allowed,
    controlled_execute_operation: data?.data?.controlled_execute_operation,
    second_confirmation_required: data?.data?.second_confirmation_required,
    owner_gate_clear: data?.data?.owner_decision_gate?.clear,
    owner_decision_requests: data?.data?.owner_decision_gate?.decision_requests || [],
    source_path: data?.data?.source_path,
    target_path: data?.data?.target_path,
    rendered_markdown: data?.data?.rendered_markdown,
    planner_review_summary: data?.data?.planner_review_summary,
    planned_writes: data?.planned_writes || [],
    warnings: data?.warnings || [],
    blocking_reasons: data?.blocking_reasons || [],
    needs_owner_reasons: data?.needs_owner_reasons || [],
    execute_blocking_reasons: data?.execute_blocking_reasons || [],
    errors: data?.errors || [],
  };
}

async function runApprovedPlannerDraftDryRun() {
  const el = document.getElementById("approved-planner-draft-result");
  const payload = approvedPlannerDraftPublishPayload();
  if (!payload.actor || !payload.path) {
    el.textContent = JSON.stringify({ ok: false, message: "actor and planner draft path are required." }, null, 2);
    return null;
  }
  document.getElementById("approved-planner-draft-confirmed").checked = false;
  document.getElementById("approved-planner-draft-confirm").disabled = true;
  latestApprovedPlannerDraftDryRun = null;
  setPanelLoading("approved-planner-draft-result");
  try {
    const response = await fetch("/api/planner-draft/publish/dry-run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    latestApprovedPlannerDraftDryRun = data;
    latestDebug["/api/planner-draft/publish/dry-run"] = data;
    el.textContent = JSON.stringify(summarizeApprovedPlannerDraftPublish(data), null, 2);
    updateApprovedPlannerDraftConfirmState();
    updateDebugPanel();
    return data;
  } catch (err) {
    setPanelError("approved-planner-draft-result", err);
    return { ok: false, error: String(err) };
  }
}

async function confirmApprovedPlannerDraftPublish() {
  const el = document.getElementById("approved-planner-draft-result");
  const actor = document.getElementById("preview-actor").value.trim();
  const confirmed = document.getElementById("approved-planner-draft-confirmed").checked;
  if (!latestApprovedPlannerDraftDryRun?.dry_run_id || !actor || !confirmed) {
    el.textContent = JSON.stringify({ ok: false, message: "Dry-run token, actor, and explicit second confirmation are required." }, null, 2);
    return null;
  }
  document.getElementById("approved-planner-draft-confirm").disabled = true;
  setPanelLoading("approved-planner-draft-result");
  try {
    const response = await fetch("/api/execute/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        dry_run_id: latestApprovedPlannerDraftDryRun.dry_run_id,
        actor,
        owner_confirmed: true,
      }),
    });
    const data = await response.json();
    latestDebug["/api/execute/confirm:approved_planner_draft_publish"] = data;
    el.textContent = JSON.stringify(summarizeExecute(data), null, 2);
    updateDebugPanel();
    await refreshPanel("drafts");
    await refreshPanel("planner-drafts-review");
    await refreshPanel("queue");
    await refreshPanel("validate");
    return data;
  } catch (err) {
    setPanelError("approved-planner-draft-result", err);
    return { ok: false, error: String(err) };
  }
}

async function reviewPlannerDraftPath(path, { allowPublishHandoff = true } = {}) {
  const el = document.getElementById("planner-draft-result");
  if (!path) {
    el.textContent = JSON.stringify({ ok: false, message: "planner draft path is required." }, null, 2);
    return null;
  }
  document.getElementById("planner-draft-path").value = path;
  document.getElementById("planner-draft-load-publish").disabled = true;
  latestPlannerDraftReview = null;
  setPanelLoading("planner-draft-result");
  try {
    const response = await fetch("/api/planner-draft/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actor: document.getElementById("preview-actor").value.trim(), path }),
    });
    const data = await response.json();
    latestPlannerDraftReview = data;
    latestDebug["/api/planner-draft/review"] = data;
    const handoffEnabled = allowPublishHandoff && Boolean(data?.data?.handoff_to_draft_publish?.enabled);
    document.getElementById("planner-draft-load-publish").disabled = !handoffEnabled;
    el.textContent = JSON.stringify(summarizePlannerDraftReview(data), null, 2);
    renderPlannerDraftReviewDetail(data);
    updateDebugPanel();
    return data;
  } catch (err) {
    setPanelError("planner-draft-result", err);
    return { ok: false, error: String(err) };
  }
}

async function reviewPlannerDraft() {
  const payload = plannerDraftReviewPayload();
  return reviewPlannerDraftPath(payload.path, { allowPublishHandoff: true });
}

function loadPlannerDraftIntoPublish() {
  const path = latestPlannerDraftReview?.data?.handoff_to_draft_publish?.path;
  if (!path) {
    return;
  }
  document.getElementById("draft-publish-path").value = path;
  document.getElementById("draft-publish-result").textContent = JSON.stringify({
    ok: true,
    message: "Planner draft path loaded. Run Draft Publish dry-run before confirming.",
    path,
  }, null, 2);
}

async function previewParentRequirement() {
  const el = document.getElementById("parent-preview-result");
  const payload = parentRequirementPayload();
  if (!payload.title || !payload.owner_goal || !payload.forum_thread_ref) {
    el.textContent = JSON.stringify({ ok: false, message: "title, owner_goal, and forum_thread_ref are required." }, null, 2);
    return null;
  }
  setPanelLoading("parent-preview-result");
  try {
    const response = await fetch("/api/parent-requirement/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    latestDebug["/api/parent-requirement/preview"] = data;
    el.textContent = JSON.stringify(summarizeParentRequirement(data), null, 2);
    updateDebugPanel();
    return data;
  } catch (err) {
    setPanelError("parent-preview-result", err);
    return { ok: false, error: String(err) };
  }
}

async function previewPlannerTickWithEndpoint(endpoint, debugKey, summarizer, renderCard = null, resultId = "planner-tick-result") {
  const el = document.getElementById(resultId);
  const payload = plannerTickPayload();
  if (!payload.orchestration_id || !payload.parent_task_id || !payload.forum_thread_ref || !payload.decision || !payload.decision_reason || !payload.next_expected_action) {
    el.textContent = JSON.stringify({ ok: false, message: "orchestration_id, parent_task_id, forum_thread_ref, decision, decision_reason, and next_expected_action are required." }, null, 2);
    return null;
  }
  setPanelLoading(resultId);
  if (renderCard) {
    renderCard(null);
  }
  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    latestDebug[debugKey] = data;
    el.textContent = JSON.stringify(summarizer(data), null, 2);
    if (renderCard) {
      renderCard(data);
    }
    updateDebugPanel();
    return data;
  } catch (err) {
    setPanelError(resultId, err);
    return { ok: false, error: String(err) };
  }
}

async function previewPlannerTick() {
  return previewPlannerTickWithEndpoint("/api/planner-tick/preview", "/api/planner-tick/preview", summarizePlannerTick);
}

async function previewManualPlannerTickFlow() {
  const data = await previewPlannerTickWithEndpoint(
    "/api/planner-tick/manual-flow/preview",
    "/api/planner-tick/manual-flow/preview",
    summarizeManualPlannerTick,
    renderManualPlannerTickCard,
    "manual-planner-tick-result"
  );
  latestManualPlannerTickPreview = data;
  return data;
}

function loadPlannerLoopPersistenceIteration() {
  const result = document.getElementById("planner-persist-result");
  const iteration = latestManualPlannerTickPreview?.data?.planner_iteration;
  if (!iteration) {
    result.textContent = JSON.stringify({ ok: false, message: "Run Manual Planner Tick preview first." }, null, 2);
    return;
  }
  document.getElementById("planner-persist-operation").value = "planner_iteration_append";
  document.getElementById("planner-persist-actor").value = iteration.planner_agent_instance || document.getElementById("preview-actor").value.trim() || "dev.codex.local";
  document.getElementById("planner-persist-owner-confirmed").checked = false;
  document.getElementById("planner-persist-confirm").disabled = true;
  document.getElementById("planner-persist-refresh-handoff").disabled = true;
  latestPlannerLoopPersistenceDryRun = null;
  latestPlannerLoopPersistenceResult = null;
  renderPlannerLoopPersistenceHandoff(null);
  document.getElementById("planner-persist-payload").value = JSON.stringify(iteration, null, 2);
  result.textContent = JSON.stringify({
    ok: true,
    operation: "planner_iteration_append",
    message: "Manual tick planner iteration loaded. Run dry-run before confirming append.",
    iteration_id: iteration.iteration_id,
    orchestration_id: iteration.orchestration_id,
  }, null, 2);
}

function loadPlannerLoopPersistenceEvent() {
  const result = document.getElementById("planner-persist-result");
  const events = latestManualPlannerTickPreview?.data?.event_log_preview || [];
  const event = events[events.length - 1];
  if (!event) {
    result.textContent = JSON.stringify({ ok: false, message: "Run Manual Planner Tick preview first." }, null, 2);
    return;
  }
  document.getElementById("planner-persist-operation").value = "orchestration_event_append";
  document.getElementById("planner-persist-actor").value = event.actor || document.getElementById("preview-actor").value.trim() || "dev.codex.local";
  document.getElementById("planner-persist-owner-confirmed").checked = false;
  document.getElementById("planner-persist-confirm").disabled = true;
  document.getElementById("planner-persist-refresh-handoff").disabled = true;
  latestPlannerLoopPersistenceDryRun = null;
  latestPlannerLoopPersistenceResult = null;
  renderPlannerLoopPersistenceHandoff(null);
  document.getElementById("planner-persist-payload").value = JSON.stringify(event, null, 2);
  result.textContent = JSON.stringify({
    ok: true,
    operation: "orchestration_event_append",
    message: "Manual tick event loaded. Run dry-run before confirming append.",
    event_id: event.event_id,
    orchestration_id: event.orchestration_id,
  }, null, 2);
}

function updatePlannerLoopPersistenceConfirmState() {
  const button = document.getElementById("planner-persist-confirm");
  const confirmed = document.getElementById("planner-persist-owner-confirmed").checked;
  button.disabled = !(
    latestPlannerLoopPersistenceDryRun?.dry_run_id
    && latestPlannerLoopPersistenceDryRun?.execute_allowed
    && latestPlannerLoopPersistenceDryRun?.verdict !== "BLOCK"
    && confirmed
  );
}

async function runPlannerLoopPersistenceDryRun() {
  const el = document.getElementById("planner-persist-result");
  const payload = plannerLoopPersistencePayload();
  if (payload?.ok === false || !payload.actor || !payload.operation) {
    el.textContent = JSON.stringify(payload?.ok === false ? payload : { ok: false, message: "operation, actor, and payload JSON are required." }, null, 2);
    return null;
  }
  document.getElementById("planner-persist-owner-confirmed").checked = false;
  document.getElementById("planner-persist-confirm").disabled = true;
  latestPlannerLoopPersistenceDryRun = null;
  latestPlannerLoopPersistenceResult = null;
  renderPlannerLoopPersistenceHandoff(null);
  setPanelLoading("planner-persist-result");
  try {
    const response = await fetch("/api/execute/dry-run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    latestPlannerLoopPersistenceDryRun = data;
    latestDebug[`/api/execute/dry-run:${payload.operation}`] = data;
    el.textContent = JSON.stringify(summarizeExecute(data), null, 2);
    renderPlannerLoopPersistenceHandoff(data);
    updatePlannerLoopPersistenceConfirmState();
    updateDebugPanel();
    return data;
  } catch (err) {
    setPanelError("planner-persist-result", err);
    return { ok: false, error: String(err) };
  }
}

async function confirmPlannerLoopPersistenceAppend() {
  const el = document.getElementById("planner-persist-result");
  const actor = document.getElementById("planner-persist-actor").value.trim();
  const confirmed = document.getElementById("planner-persist-owner-confirmed").checked;
  if (!latestPlannerLoopPersistenceDryRun?.dry_run_id || !actor || !confirmed) {
    el.textContent = JSON.stringify({ ok: false, message: "Dry-run token, actor, and explicit Owner confirmation are required." }, null, 2);
    return null;
  }
  document.getElementById("planner-persist-confirm").disabled = true;
  setPanelLoading("planner-persist-result");
  try {
    const response = await fetch("/api/execute/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        dry_run_id: latestPlannerLoopPersistenceDryRun.dry_run_id,
        actor,
        owner_confirmed: true,
      }),
    });
    const data = await response.json();
    latestDebug[`/api/execute/confirm:${latestPlannerLoopPersistenceDryRun.operation}`] = data;
    latestPlannerLoopPersistenceResult = data;
    if (data?.ok) {
      latestPlannerLoopPersistenceDryRun = null;
      document.getElementById("planner-persist-owner-confirmed").checked = false;
    }
    el.textContent = JSON.stringify(summarizeExecute(data), null, 2);
    renderPlannerLoopPersistenceHandoff(data);
    updateDebugPanel();
    await refreshPanel("records");
    await refreshPlannerLoopPersistenceHandoffViews();
    return data;
  } catch (err) {
    setPanelError("planner-persist-result", err);
    return { ok: false, error: String(err) };
  }
}

async function refreshPlannerLoopPersistenceHandoffViews() {
  const source = latestPlannerLoopPersistenceResult || latestPlannerLoopPersistenceDryRun;
  const orchestrationId = plannerPersistenceOrchestrationId(source);
  if (!orchestrationId) {
    document.getElementById("planner-persist-result").textContent = JSON.stringify({
      ok: false,
      message: "No orchestration_id available for handoff refresh.",
    }, null, 2);
    return null;
  }
  document.getElementById("orchestration-timeline-id").value = orchestrationId;
  document.getElementById("orchestration-summary-id").value = orchestrationId;
  document.getElementById("context-pack-source").value = "orchestration_id";
  document.getElementById("context-pack-selector").value = orchestrationId;
  document.getElementById("planner-loop-id").value = orchestrationId;
  await loadOrchestrationTimeline();
  await loadOrchestrationSummary();
  await loadContextPackPreview();
  await loadPlannerLoopMvp();
  renderPlannerLoopPersistenceHandoff(source);
  return source;
}

async function reviewOwnerDecisionResolution() {
  const el = document.getElementById("owner-resolution-result");
  const payload = ownerDecisionResolutionPayload();
  if (!payload.request_id || !payload.orchestration_id || !payload.actor || !payload.forum_thread_ref || !payload.evidence_ref || !payload.decision || !payload.decision_reason) {
    el.textContent = JSON.stringify({ ok: false, message: "request_id, orchestration_id, actor, forum_thread_ref, evidence_ref, decision, and decision_reason are required." }, null, 2);
    return null;
  }
  setPanelLoading("owner-resolution-result");
  renderOwnerDecisionResolution(null);
  try {
    const response = await fetch("/api/owner-decision/resolve/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    latestOwnerDecisionResolutionReview = data;
    latestDebug["/api/owner-decision/resolve/review"] = data;
    el.textContent = JSON.stringify(summarizeOwnerDecisionResolution(data), null, 2);
    renderOwnerDecisionResolution(data);
    updateDebugPanel();
    return data;
  } catch (err) {
    setPanelError("owner-resolution-result", err);
    renderOwnerDecisionResolution(null);
    return { ok: false, error: String(err) };
  }
}

function summarizeOwnerDecisionRecordExecute(data) {
  return {
    ok: data?.ok,
    verdict: data?.verdict,
    operation: data?.operation,
    actor: data?.actor?.actor,
    dry_run_id: data?.dry_run_id,
    dry_run_snapshot_hash: data?.dry_run_snapshot_hash,
    execute_allowed: data?.execute_allowed,
    decision_id: data?.summary?.decision_id || data?.data?.decision_id,
    target_path: data?.summary?.target_path || data?.data?.target_path,
    would_write: data?.summary?.would_write || data?.data?.would_write,
    wrote: data?.summary?.wrote || data?.data?.wrote,
    planned_writes: data?.planned_writes || [],
    performed_writes: data?.performed_writes || [],
    warnings: data?.warnings || [],
    blocking_reasons: data?.blocking_reasons || [],
    execute_blocking_reasons: data?.execute_blocking_reasons || [],
    errors: data?.errors || [],
  };
}

async function runOwnerDecisionRecordDryRun() {
  const el = document.getElementById("owner-record-result");
  const payload = ownerDecisionRecordPayload();
  if (!payload.actor || !payload.payload.decision_id || !payload.payload.decision_summary || !payload.payload.applies_to.project || !payload.payload.owner_approval_evidence.evidence_id) {
    el.textContent = JSON.stringify({ ok: false, message: "actor, decision_id, project, evidence_id, and decision_summary are required." }, null, 2);
    return null;
  }
  latestOwnerDecisionRecordDryRun = null;
  document.getElementById("owner-record-confirm").disabled = true;
  setPanelLoading("owner-record-result");
  try {
    const response = await fetch("/api/execute/dry-run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    latestOwnerDecisionRecordDryRun = data;
    latestDebug["/api/execute/dry-run:owner_decision_record"] = data;
    document.getElementById("owner-record-confirm").disabled = !Boolean(data?.dry_run_id && data?.execute_allowed && data?.verdict !== "BLOCK");
    el.textContent = JSON.stringify(summarizeOwnerDecisionRecordExecute(data), null, 2);
    updateDebugPanel();
    return data;
  } catch (err) {
    setPanelError("owner-record-result", err);
    return { ok: false, error: String(err) };
  }
}

async function confirmOwnerDecisionRecord() {
  const el = document.getElementById("owner-record-result");
  const actor = ownerRecordValue("owner-record-actor");
  if (!latestOwnerDecisionRecordDryRun?.dry_run_id || !actor) {
    el.textContent = JSON.stringify({ ok: false, message: "Owner decision record dry-run token and actor are required." }, null, 2);
    return null;
  }
  document.getElementById("owner-record-confirm").disabled = true;
  setPanelLoading("owner-record-result");
  try {
    const response = await fetch("/api/execute/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        dry_run_id: latestOwnerDecisionRecordDryRun.dry_run_id,
        actor,
      }),
    });
    const data = await response.json();
    latestDebug["/api/execute/confirm:owner_decision_record"] = data;
    el.textContent = JSON.stringify(summarizeOwnerDecisionRecordExecute(data), null, 2);
    updateDebugPanel();
    await refreshPanel("owner-decision-records");
    await refreshPanel("records");
    return data;
  } catch (err) {
    setPanelError("owner-record-result", err);
    return { ok: false, error: String(err) };
  }
}

async function reviewForumEvent() {
  const el = document.getElementById("forum-event-result");
  const payload = forumEventReviewPayload();
  if (!payload.orchestration_id || !payload.event_type || !payload.actor || !payload.summary || !payload.forum_thread_ref) {
    el.textContent = JSON.stringify({ ok: false, message: "orchestration_id, event_type, actor, summary, and forum_thread_ref are required." }, null, 2);
    return null;
  }
  setPanelLoading("forum-event-result");
  try {
    const response = await fetch("/api/forum-event/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    latestDebug["/api/forum-event/review"] = data;
    el.textContent = JSON.stringify(summarizeForumEventReview(data), null, 2);
    updateDebugPanel();
    return data;
  } catch (err) {
    setPanelError("forum-event-result", err);
    return { ok: false, error: String(err) };
  }
}

async function refreshPanel(id) {
  const path = ROUTE_BY_ID[id];
  if (!path) {
    return null;
  }
  const data = await loadRoute(id, path);
  latestDebug[path] = data;
  updateDebugPanel();
  return data;
}

function updateDebugPanel() {
  document.getElementById("debug").textContent = JSON.stringify(latestDebug, null, 2);
}

function setOrchestrationSummaryRawVisible(visible) {
  const panel = document.getElementById("orchestration-summary");
  const toggle = document.getElementById("orchestration-summary-raw-toggle");
  panel.hidden = !visible;
  toggle.textContent = visible ? "Hide Raw JSON" : "Show Raw JSON";
  toggle.setAttribute("aria-expanded", String(visible));
}

function toggleOrchestrationSummaryRaw() {
  const panel = document.getElementById("orchestration-summary");
  setOrchestrationSummaryRawVisible(panel.hidden);
}

function setDraftPublishRawVisible(visible) {
  const panel = document.getElementById("draft-publish-result");
  const toggle = document.getElementById("draft-publish-raw-toggle");
  panel.hidden = !visible;
  toggle.textContent = visible ? "Hide Raw Publish JSON" : "Show Raw Publish JSON";
  toggle.setAttribute("aria-expanded", String(visible));
}

function toggleDraftPublishRaw() {
  const panel = document.getElementById("draft-publish-result");
  setDraftPublishRawVisible(panel.hidden);
}

function toggleDebug() {
  const debug = document.getElementById("debug");
  const toggle = document.getElementById("debug-toggle");
  const nextHidden = !debug.hidden;
  debug.hidden = nextHidden;
  toggle.textContent = nextHidden ? "Show Debug" : "Hide Debug";
  toggle.setAttribute("aria-expanded", String(!nextHidden));
}

function restoreActorInput() {
  const actorInput = document.getElementById("preview-actor");
  const stored = localStorage.getItem(ACTOR_STORAGE_KEY);
  if (stored) {
    actorInput.value = stored;
  }
  actorInput.addEventListener("input", () => {
    localStorage.setItem(ACTOR_STORAGE_KEY, actorInput.value.trim());
  });
}

async function refreshAll() {
  const debug = {};
  await loadOrchestrationIndex();
  for (const [id, path] of ROUTES) {
    debug[path] = await loadRoute(id, path);
  }
  debug["/api/orchestration/index"] = latestDebug["/api/orchestration/index"];
  latestDebug = debug;
  updateDebugPanel();
  if (selectedTask) {
    await loadTaskDetail();
  }
  if (latestOrchestrationSummary) {
    await loadOrchestrationSummary();
  }
  if (latestOrchestrationTimeline) {
    await loadOrchestrationTimeline();
  }
  if (latestContextPackPreview) {
    await loadContextPackPreview();
  }
}

document.getElementById("refresh").addEventListener("click", refreshAll);
for (const button of document.querySelectorAll(".panel-refresh")) {
  button.addEventListener("click", () => refreshPanel(button.dataset.routeId));
}
document.getElementById("load-task").addEventListener("click", loadTaskDetail);
document.getElementById("load-preview").addEventListener("click", loadTaskPreview);
document.getElementById("load-orchestration-summary").addEventListener("click", loadOrchestrationSummary);
document.getElementById("orchestration-summary-raw-toggle").addEventListener("click", toggleOrchestrationSummaryRaw);
document.getElementById("load-orchestration-timeline").addEventListener("click", loadOrchestrationTimeline);
document.getElementById("load-planner-loop").addEventListener("click", loadPlannerLoopMvp);
document.getElementById("context-pack-load-selected").addEventListener("click", loadSelectedTaskForContextPack);
document.getElementById("context-pack-preview").addEventListener("click", loadContextPackPreview);
document.getElementById("execute-dry-run").addEventListener("click", runExecuteDryRun);
document.getElementById("execute-confirm").addEventListener("click", confirmExecute);
document.getElementById("draft-dry-run").addEventListener("click", runDraftDryRun);
document.getElementById("draft-create-confirm").addEventListener("click", confirmDraftCreate);
document.getElementById("draft-publish-dry-run").addEventListener("click", runDraftPublishDryRun);
document.getElementById("draft-publish-confirm").addEventListener("click", confirmDraftPublish);
document.getElementById("draft-publish-raw-toggle").addEventListener("click", toggleDraftPublishRaw);
document.getElementById("planner-draft-review").addEventListener("click", reviewPlannerDraft);
document.getElementById("planner-draft-load-publish").addEventListener("click", loadPlannerDraftIntoPublish);
document.getElementById("approved-planner-draft-dry-run").addEventListener("click", runApprovedPlannerDraftDryRun);
document.getElementById("approved-planner-draft-confirm").addEventListener("click", confirmApprovedPlannerDraftPublish);
document.getElementById("approved-planner-draft-confirmed").addEventListener("change", updateApprovedPlannerDraftConfirmState);
document.getElementById("parent-preview").addEventListener("click", previewParentRequirement);
document.getElementById("planner-tick-preview").addEventListener("click", previewPlannerTick);
document.getElementById("manual-planner-tick-preview").addEventListener("click", previewManualPlannerTickFlow);
document.getElementById("planner-persist-load-iteration").addEventListener("click", loadPlannerLoopPersistenceIteration);
document.getElementById("planner-persist-load-event").addEventListener("click", loadPlannerLoopPersistenceEvent);
document.getElementById("planner-persist-dry-run").addEventListener("click", runPlannerLoopPersistenceDryRun);
document.getElementById("planner-persist-confirm").addEventListener("click", confirmPlannerLoopPersistenceAppend);
document.getElementById("planner-persist-owner-confirmed").addEventListener("change", updatePlannerLoopPersistenceConfirmState);
document.getElementById("planner-persist-refresh-handoff").addEventListener("click", refreshPlannerLoopPersistenceHandoffViews);
document.getElementById("external-intake-prefill-resolution").addEventListener("click", prefillOwnerResolutionFromExternalIntake);
document.getElementById("external-intake-load-publish").addEventListener("click", loadExternalIntakeIntoDraftPublish);
document.getElementById("owner-decision-record-prefill-resolution").addEventListener("click", prefillOwnerResolutionFromDecisionRecord);
document.getElementById("owner-resolution-load-selected").addEventListener("click", loadSelectedOwnerDecisionForResolution);
document.getElementById("owner-resolution-review").addEventListener("click", reviewOwnerDecisionResolution);
document.getElementById("owner-record-load-resolution").addEventListener("click", loadOwnerRecordFromResolution);
document.getElementById("owner-record-dry-run").addEventListener("click", runOwnerDecisionRecordDryRun);
document.getElementById("owner-record-confirm").addEventListener("click", confirmOwnerDecisionRecord);
document.getElementById("forum-event-review").addEventListener("click", reviewForumEvent);
document.getElementById("ai-author-preview").addEventListener("click", previewAiAuthorDraft);
document.getElementById("ai-author-discard").addEventListener("click", invalidateAiAuthorPreview);
document.getElementById("ai-author-owner-confirmed").addEventListener("change", updateAiAuthorConfirmState);
document.getElementById("ai-author-confirm").addEventListener("click", confirmAiAuthorDraft);
for (const input of document.querySelectorAll('input[name="ai-author-mode"]')) {
  input.addEventListener("change", updateAiAuthorModeFields);
}
document.getElementById("profile-action").addEventListener("change", updateProfileActionFields);
document.getElementById("profile-draft").addEventListener("click", previewProfileDraft);
document.getElementById("profile-discard").addEventListener("click", invalidateProfilePreview);
document.getElementById("profile-owner-confirmed").addEventListener("change", updateProfileConfirmState);
document.getElementById("profile-confirm").addEventListener("click", confirmProfileDraft);
for (const input of document.querySelectorAll("#custom-agent-profiles input, #custom-agent-profiles select, #custom-agent-profiles textarea")) {
  if (input.id !== "profile-owner-confirmed") {
    input.addEventListener("input", invalidateProfilePreview);
    input.addEventListener("change", invalidateProfilePreview);
  }
}
for (const input of document.querySelectorAll("#ai-task-authoring input, #ai-task-authoring select, #ai-task-authoring textarea")) {
  if (input.id !== "ai-author-owner-confirmed") {
    input.addEventListener("input", invalidateAiAuthorPreview);
    input.addEventListener("change", invalidateAiAuthorPreview);
  }
}
document.getElementById("needs-owner-load-task").addEventListener("click", loadTaskFromNeedsOwner);
document.getElementById("validation-load-task").addEventListener("click", loadTaskFromValidation);
document.getElementById("debug-toggle").addEventListener("click", toggleDebug);
restoreActorInput();
updateAiAuthorModeFields();
updateProfileActionFields();
refreshAll();
