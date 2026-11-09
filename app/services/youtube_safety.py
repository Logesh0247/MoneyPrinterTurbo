"""Fail-closed YouTube automation safeguards.

The current project does not implement official YouTube OAuth uploading or
verification of all mandatory quality checks. Consequently no configuration,
score or user assertion may mark a video ready for automatic publishing here.
"""

REQUIRED_CHECKS = (
    "Audio/video sync and drift",
    "Black frames, freezes, glitches, clipping, pops and noise",
    "Faces, hands, text, flicker and warping",
    "Loudness and voice clarity",
    "Subtitle accuracy, legibility, timing and spelling",
    "Color and exposure consistency",
    "Facts verified and sources recorded",
    "Asset rights and copyright review",
    "Content safety review",
    "Originality against channel history",
    "Length, aspect ratio, resolution and format",
)

BLOCK_REASON = (
    "YouTube publishing is blocked: official YouTube Data API OAuth upload, "
    "mandatory quality verification and approval controls are not implemented."
)


# The legacy Upload-Post adapter can send these explicitly named non-YouTube
# targets. Empty, malformed or unknown platform IDs must not reach the provider:
# the provider might otherwise interpret an omitted target as its defaults.
_NON_YOUTUBE_TARGETS = frozenset({"tiktok", "instagram", "facebook", "linkedin"})


def is_youtube_target(platforms):
    """Block YouTube and any ambiguous target at the legacy network boundary."""
    if not isinstance(platforms, (list, tuple)) or not platforms:
        return True
    return any(not isinstance(p, str) or p not in _NON_YOUTUBE_TARGETS for p in platforms)


def official_youtube_publishing_available():
    """No compliant official uploader/mandatory gate exists yet."""
    return False


def safety_report():
    """Unknown checks are not passes. Never manufacture a quality score."""
    return {
        "mode": "SAFE",
        "status": "BLOCKED",
        "quality_score": None,
        "checks": {name: "NOT VERIFIED" for name in REQUIRED_CHECKS},
        "reason": BLOCK_REASON,
    }
