# AIPOS-194 Provenance Authority Gap Detection Implementation Notes

Status: implementation note

This note records the AIPOS-194 step-0 provenance audit before implementation.

## Current Durable Provenance Writers

`draft_publish`
: Before AIPOS-194, `draft_publish` wrote only the pending task card under
`5_tasks/queue/pending/` and did not write a durable publish record. AIPOS-194
adds the narrow publish provenance writer:
`5_tasks/records/publishes/<task_id>/<publish_id>.md`.

`queue_claim`
: Writes claim/session provenance when called through the with-records paths.
Claimed queue tasks are authority-valid only when the explicit claim/session
record references match durable records.

`queue_return`
: Writes return provenance through the AIPOS-175 records writer. Returned or
audit-ready tasks are authority-valid only when the explicit return record
reference matches a durable return record.

`audit_dispatch` / `audit_verdict`
: Write their own durable records and are checked by explicit task and record
references.

## Current No-Provenance Classes

`completed`
: No dedicated completion record writer exists in the current implementation.
AIPOS-194 does not retroactively invalidate completed tasks solely because that
writer is absent.

`blocked`
: No dedicated block record writer exists in the current implementation.
AIPOS-194 does not retroactively invalidate blocked tasks solely because that
writer is absent.

`drafts`
: Drafts are pre-authority inputs. They may receive `PRE_AUTHORITY_WARN`, but
they are not queue truth and are not classified as `ORPHAN_INVALID`.

## Adoption Boundary

Existing workspaces with pending tasks created before AIPOS-194 may not have
publish records. AIPOS-194 classifies such pending tasks as `QUARANTINED` with
`effective_truth: false`, not as `ORPHAN_INVALID`. A clean grandfather path for
existing workspaces requires a deferred adoption manifest captured before
untrusted agent access and held outside agent write authority.

Manifest and signature hardening remain deferred.
