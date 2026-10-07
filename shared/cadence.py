"""Cadence for periodic uploads and the freshness they imply."""

UPLOAD_INTERVAL_SECONDS = 5 * 60
STALE_AFTER_SECONDS = 2 * UPLOAD_INTERVAL_SECONDS + 150
