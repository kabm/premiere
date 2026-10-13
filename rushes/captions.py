"""Captions: SRT/VTT import/export, styled burn-in, speech-to-text.

Hook contract:
    burn_filters(project) -> list[str]   video filters (yuv444p16le canvas) applied after titles
    register(subparsers)                 optional CLI commands
"""


def burn_filters(project):
    return []


def register(sub):
    pass
