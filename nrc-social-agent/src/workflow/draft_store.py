"""S3-backed store for draft documents, fixed to the `drafts/` prefix.

Two distinct, non-colliding uses share this one prefix-scoped store:
`WorkflowDocument.draft` (reserved since Milestone 3 for the future user
"Save Draft" action, docs/WORKFLOW.md §3.9 — still unwritten, stays
`null`) would key by `<workflow_id>` alone; Milestone 6's generated
primary draft (see src/ai/draft_repository.py) keys by
`<workflow_id>/<output_id>` — a different S3 key (`drafts/<workflow_id>/
<output_id>.json` vs. `drafts/<workflow_id>.json`), so the two can never
collide even though they share this store class and prefix.
"""

from __future__ import annotations

from ..storage.json_object_store import JsonObjectStore

DRAFT_KEY_PREFIX = "drafts"


class DraftStore(JsonObjectStore):
    def __init__(self, client, bucket_name: str) -> None:
        super().__init__(client=client, bucket_name=bucket_name, key_prefix=DRAFT_KEY_PREFIX)
