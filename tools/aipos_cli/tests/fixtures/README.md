# AIPOS-39 Board Adapter Execute Fixtures

These fixtures are static integration-test inputs for `test_board_adapter_execute_integration.py`.

Rules:
- Fixtures are read-only.
- Tests must copy fixture contents into `tempfile.TemporaryDirectory()`.
- Tests must never mutate real repository paths under `5_tasks/`.
- Fixtures cover only AIPOS-38 execute allowlist behavior (`draft_create`, `draft_publish`, `queue_claim`) and blocked paths.
