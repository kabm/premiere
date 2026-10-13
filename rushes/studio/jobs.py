"""Background render queue for the Studio (one render at a time, with progress and cancel)."""

import itertools
import os
import queue
import subprocess
import threading
import time

from ..presets import get_preset
from ..project import Project
from ..render import build_command
from ..util import RushesError


class Job:
    _ids = itertools.count(1)

    def __init__(self, data, base_dir, preset, output, draft, label):
        self.id = next(self._ids)
        self.data, self.base_dir = data, base_dir
        self.preset, self.output, self.draft = preset, output, draft
        self.label = label or os.path.basename(output or "") or "Render"
        self.status = "queued"          # queued, analysing, rendering, done, failed, cancelled
        self.progress = 0.0
        self.outputs = []
        self.error = None
        self.created = time.time()
        self.finished = None
        self.proc = None
        self.cancelled = False

    def to_dict(self):
        return {"id": self.id, "label": self.label, "preset": self.preset, "status": self.status,
                "progress": round(self.progress, 1), "outputs": self.outputs, "error": self.error,
                "draft": self.draft, "created": self.created, "finished": self.finished}


class JobManager:
    def __init__(self):
        self.jobs = []
        self.lock = threading.Lock()
        self.queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    def submit(self, data, base_dir, preset, output, draft=False, label=None):
        Project(data, base_dir)  # validate now so the user gets errors immediately
        if preset:
            get_preset(preset)
        job = Job(data, base_dir, preset, output, draft, label)
        with self.lock:
            self.jobs.append(job)
        self.queue.put(job)
        return job

    def get(self, job_id):
        with self.lock:
            return next((j for j in self.jobs if j.id == job_id), None)

    def list(self):
        with self.lock:
            return [j.to_dict() for j in self.jobs]

    def busy(self):
        with self.lock:
            return any(j.status in ("queued", "analysing", "rendering") for j in self.jobs)

    def cancel(self, job_id):
        job = self.get(job_id)
        if not job:
            raise RushesError(f"No job {job_id}")
        job.cancelled = True
        if job.proc and job.proc.poll() is None:
            job.proc.terminate()
        if job.status == "queued":
            job.status = "cancelled"

    def clear_finished(self):
        with self.lock:
            self.jobs = [j for j in self.jobs if j.status in ("queued", "analysing", "rendering")]

    def _worker(self):
        while True:
            job = self.queue.get()
            if job.cancelled:
                job.status = "cancelled"
                continue
            try:
                self._run(job)
            except RushesError as exc:
                job.status, job.error = "failed", str(exc)
            except Exception as exc:  # keep the queue alive whatever happens
                job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"
            job.finished = time.time()

    def _run(self, job):
        job.status = "analysing"
        project = Project(job.data, job.base_dir)
        cmd, outputs, _ = build_command(project, job.preset, job.output, job.draft, draft_format="webm")
        if job.cancelled:
            job.status = "cancelled"
            return
        job.status = "rendering"
        cmd = cmd[:1] + ["-progress", "pipe:1", "-nostats"] + cmd[1:]
        job.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    encoding="utf-8", errors="replace")
        tail = []

        def drain():
            for line in job.proc.stderr:
                tail.append(line)
                del tail[:-40]

        t = threading.Thread(target=drain, daemon=True)
        t.start()
        total = max(project.duration, 1e-6)
        for line in job.proc.stdout:
            key, _, val = line.strip().partition("=")
            if key == "out_time_us" and val.isdigit():
                job.progress = min(99.9, int(val) / 1e6 / total * 100)
        job.proc.wait()
        t.join()
        job.proc.stdout.close()
        job.proc.stderr.close()
        if job.cancelled:
            job.status = "cancelled"
        elif job.proc.returncode != 0:
            job.status = "failed"
            job.error = "".join(tail[-12:]).strip() or f"ffmpeg exited with {job.proc.returncode}"
        else:
            job.status, job.progress, job.outputs = "done", 100.0, outputs
