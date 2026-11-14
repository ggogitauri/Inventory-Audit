"""Versioned wire contract shared by the backend and the robot-side uploader.

Bump `CONTRACT_VERSION` when a field changes in a way that old uploaders cannot
safely send (new required fields, changed meaning of a field). Bump
`ALGORITHM_VERSION` when change-detection results change, so stored reports can
always be traced back to the code/config that produced them.
"""
from __future__ import annotations

CONTRACT_VERSION = 2
ALGORITHM_VERSION = "2.0"

# Fields a pass message may carry. Everything except area/patrol/pass_idx/detections
# is optional so old mock clients keep working.
PATROL_FIELDS = ("area", "patrol", "map_version", "schema_version", "meta")
PASS_FIELDS = ("pass_idx", "n_passes", "last", "captured_at", "pose", "meta", "detections")
