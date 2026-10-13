"""Delivery extras: soft subtitles, chapter markers, burn-ins, sidecar files.

Hook contract (called from render.build_command):
    output_extras(project, preset, out, cache_dir, n_inputs) -> (extra_input_args, extra_output_args, sidecars)
        extra_input_args are appended after the graph inputs (their indexes start at n_inputs);
        extra_output_args are inserted before the output path; sidecars is a list of files written.
"""


def output_extras(project, preset, out, cache_dir, n_inputs):
    return [], [], []
