# Reporting Policy

AI Project OS distinguishes between temporary task reports and formal repository documents.

## Default Rule

Completion Reports and Review Reports are temporary execution outputs by default.

They do not enter Git by default.

They should normally be returned to the Owner directly, pasted back into the active planning conversation, or stored locally under:

```text
task_cards/reports/
```

The task_cards/ directory is local execution context and is ignored by Git.

### When Reports May Enter Git

A report may enter Git only when the Owner explicitly decides that it is a formal stage archive or long-term governance record.

Examples that may enter Git after Owner approval:

* Formal stage archive
* Final audit record with long-term value
* Approved decision record
* Reusable template or protocol

Examples that should not enter Git by default:

* Temporary Completion Report
* Temporary Review Report
* Pre-push check report
* Resume context
* Current local Git status
* Agent execution logs

### Promotion Rule

Temporary reports may contain useful information.

Useful conclusions should be promoted into formal locations such as:

* 0_control_plane/
* 1_shared_memory/
* 2_projects/<project>/
* decision logs
* stage archives

The raw temporary report itself should remain outside Git unless explicitly approved.
