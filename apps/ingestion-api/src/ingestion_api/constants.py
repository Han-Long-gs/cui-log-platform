"""HTTP-layer limits and the fixed development tenant id for the ingestion API."""

import uuid

MAX_EVENTS_PER_BATCH = 200
MAX_BODY_BYTES = 1048576
DEV_TENANT_ID = uuid.UUID("dab739d3-52a2-4efa-ae31-6afc1031f062")
