"""Audio effects (Essential Sound style): speech enhancement, EQ, dynamics, repair, reverb, pitch...

Hook contract (called from graph.GraphBuilder):
    build(cfg, project) -> list[str]   audio filters for a clip ("audio.fx"), an audio track ("fx")
                                       or the master bus ("master.audio_fx"); cfg may be None
    register(subparsers)               optional CLI commands
Filters receive 32-bit float audio at project.sample_rate with project.channels channels.
"""


def build(cfg, project):
    return []


def register(sub):
    pass
