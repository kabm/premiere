"""Timeline features: adjustment layers, time remapping / speed ramps, freeze frames, nesting.

Hook contract:
    adjustment_layers(builder, label) -> label   applied after overlays, before titles
    register(subparsers)                         optional CLI commands
"""


def adjustment_layers(builder, label):
    return label


def register(sub):
    pass
