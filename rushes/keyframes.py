"""Keyframed motion/opacity.

Hook contract:
    transform_filters(clip, project) -> list[str] | None
        Called by GraphBuilder.transform() when a clip has "keyframes"; returns the motion filters
        operating on a canvas-sized yuv444p16le frame (timeline-relative t starts at 0 for the clip).
"""


def transform_filters(clip, project):
    return None
