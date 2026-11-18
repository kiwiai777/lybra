# Task integration target branches

`target_branch` is an optional task frontmatter field. Omit it to retain the project's existing integration default. Set it to an **existing local branch** in the card's registered `lane.repo` to isolate a release line. Names such as revision expressions, `refs/...`, `HEAD`, and task branches are refused.

```yaml
lane:
  repo: android
  paths: [app/]
target_branch: ota
```

The target is immutable after claim. Claim records bind it; returns bind the target and candidate commit; verdict consumption requires that exact returned subject. Derived audits, repairs, and follow-up cards inherit it. Carrying a branch to a card in a different repository or with a different integration target is refused. `fix_base_branch` remains the source checkout/diff base for repairs and does not replace the integration destination.

Claim worktrees start at the selected target. Return scope and test discovery, audit detached worktrees, kickoff baseline hints, and finalize all follow the same metadata resolver. Finalize merges only into that target and records it. A repeated finalize does not merge twice. Non-default targets cannot use `--deploy`; the generated next command omits deployment. If the repository has an applicable default deployment mechanism, finalize refuses before checkout/merge even without `--deploy`, because its existing automatic deployment path is not a multi-branch deployment contract. OTA repositories declared `no_deploy` (or with no deployment mechanism) can merge normally. Default-branch push behavior remains unchanged. Non-default targets refuse before checkout/merge when `--push` could actually push to an existing remote tracking target; implicit Git configuration is not accepted as proof of one authorized destination ref (including matching, remote.push, or an upstream mapped to main). Omit `--push` for local integration only; this does not claim remote delivery. Without origin or without origin/<target>, existing push-not-applicable behavior remains. Initializing a branch never pushes.

## Initialize a release branch

Use the registered repository **name** and a full 40-character commit SHA. Inspect the preview, then confirm using an Owner credential from the declared connection file. This uses the existing authenticated `owner_decision_record` gate, including its Owner-only confirmation, snapshot replay, decision record, and governance landing. An actor label alone grants no authority.

```sh
lybra --workspace-root <governance-root> project init-branch \
  --repo android --branch ota --start-commit <full-40-character-sha> --actor owner
lybra --workspace-root <governance-root> project init-branch \
  --repo android --branch ota --start-commit <same-full-sha> --actor owner --confirm
```

Repeat per registered repository with its own reviewed start SHA. Preview is the default and writes nothing. An existing branch at exactly that SHA is an unchanged no-op. A branch at another SHA is refused; there is no force/reset option. A failure after an authorization receipt is written retains that receipt and reports the actual failure; it never moves an existing branch to recover.

No command in this document updates a customer remote. Actual initialization needs the reviewed target repository/SHA and existing Owner authorization. This feature does not change the project's global integration/deployment default.

## Recover an existing multi-repository audit

A derived audit (including a supplementary audit) may omit `lane.repo`. Its repository follows the existing `forensic_subject` source (`reviewed_task_id`, then `derived_from`), rather than the project's default repository. An explicit audit lane must match that source. Missing sources, source cycles, conflicting repositories or integration targets remain refusals; branch existence and claim bindings are still checked.

For the existing HBJOTA-64 / HBJOTA-64R case, after this fix is formally audited and deployed, resume the original HBJOTA-64 loop with its original actor and envelope so it claims the existing pending HBJOTA-64R. Keep the original return, dispatch and failure evidence. Do not create a replacement audit, manually create an `ota` branch in the default web repository, or change the integration target to `main`. A legacy pending audit without a lane needs no manual metadata repair; an already declared matching business lane also remains valid. Independent audit is still required before delivery.
