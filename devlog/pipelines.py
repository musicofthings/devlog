"""Find Nextflow and Snakemake runs so day notes show analysis work, not just coding.

Both engines leave a record in the directory a run was launched from:

- Nextflow appends one line per run to `.nextflow/history`
  (`timestamp  duration  run_name  status  revision  session_id  command`,
  tab-separated, local time) and writes `.nextflow.log` (rotated to
  `.nextflow.log.1` … `.9`), which has the resolved revision and the error.
- Snakemake writes one `.snakemake/log/<YYYY-MM-DDTHHMMSS.ffffff>.snakemake.log`
  per invocation.

Launch directories are found under each project root (two levels deep,
skipping `work/`, `results/` and environments) and under `pipeline_dirs` from
config.toml. Everything here is read-only and stays in the private vault.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

from devlog.privacy import redact_sensitive_text

MAX_SCAN_DEPTH = 2
MAX_SCAN_DIRS = 400
ERROR_CHARS = 160
# Heavy or irrelevant directories: Nextflow's work/ alone can hold millions of files.
_SKIP_DIRS = {
    "work", "results", "node_modules", "__pycache__", "venv", ".venv", "env", ".git",
    "site-packages", "conda", ".conda", ".snakemake", ".nextflow", "singularity", ".cache",
}
_DURATION_RE = re.compile(r"(?P<n>\d+(?:\.\d+)?)\s*(?P<unit>ms|d|h|m|s)\b")
_LAUNCH_RE = re.compile(
    r"Launching `(?P<pipeline>[^`]+)` \[(?P<run>[^\]]+)\].*?"
    r"(?:revision: (?P<rev>\w+)(?: \[(?P<branch>[^\]]+)\])?)?\s*$"
)
_NF_ERROR_RE = re.compile(r"ERROR ~ (?P<msg>.+)$")
_NF_CAUSE_RE = re.compile(r"Session aborted -- Cause: (?P<msg>.+)$")
_SMK_LOG_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{6})(?:\.\d+)?\.snakemake\.log$")
_SMK_ERROR_RE = re.compile(r"^(?:Error in rule (?P<rule>[\w.-]+)|(?P<other>\w*Error\b.*))")
_SLUG_BAD_RE = re.compile(r"[^a-z0-9._-]+")


@dataclass
class PipelineRun:
    engine: str  # "nextflow" | "snakemake"
    pipeline: str  # "nf-core/sarek", or the Snakemake workflow folder
    status: str  # "success" | "failed" | "running" | "incomplete"
    start: datetime
    minutes: float | None = None
    run_name: str | None = None
    version: str | None = None
    profile: str | None = None
    error: str | None = None
    launch_dir: str = ""

    @property
    def slug(self) -> str:
        return pipeline_slug(self.pipeline)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["start"] = self.start.isoformat(timespec="minutes")
        data["slug"] = self.slug
        if data["minutes"] is not None:
            data["minutes"] = round(data["minutes"], 1)
        return data


def pipeline_slug(name: str) -> str:
    """'nf-core/sarek' -> 'nf-core-sarek' (file- and tag-safe)."""
    slug = _SLUG_BAD_RE.sub("-", name.strip().lower()).strip("-.")
    return slug or "pipeline"


def parse_duration(text: str) -> float | None:
    """Nextflow duration ('1h 2m 3s', '45.2s', '350ms', '1d 4h') in minutes."""
    per_unit = {"d": 1440.0, "h": 60.0, "m": 1.0, "s": 1 / 60, "ms": 1 / 60000}
    total, found = 0.0, False
    for match in _DURATION_RE.finditer(text or ""):
        total += float(match.group("n")) * per_unit[match.group("unit")]
        found = True
    return total if found else None


def _clean_error(text: str) -> str:
    text = redact_sensitive_text(re.sub(r"\s+", " ", text)).strip()
    if len(text) > ERROR_CHARS:
        text = text[: ERROR_CHARS - 1].rstrip() + "…"
    return text


def _command_args(command: str) -> tuple[str | None, str | None, str | None]:
    """(pipeline, revision, profile) from `nextflow run <pipeline> -r x -profile y …`."""
    tokens = command.split()
    pipeline = revision = profile = None
    try:
        start = tokens.index("run") + 1
    except ValueError:
        return None, None, None
    i = start
    while i < len(tokens):
        tok = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if tok in {"-r", "-revision"} and nxt:
            revision, i = nxt, i + 2
            continue
        if tok == "-profile" and nxt:
            profile, i = nxt, i + 2
            continue
        if tok.startswith("-"):
            # Options with a value (-c x, --input x) — skip the value unless it's a flag.
            i += 2 if nxt is not None and not nxt.startswith("-") and pipeline else 1
            continue
        if pipeline is None:
            pipeline = tok
        i += 1
    return pipeline, revision, profile


def _nextflow_log_details(launch_dir: Path) -> dict[str, dict]:
    """run_name -> {pipeline, version, error} from .nextflow.log and its rotations."""
    details: dict[str, dict] = {}
    logs = [launch_dir / ".nextflow.log"]
    logs += [launch_dir / f".nextflow.log.{i}" for i in range(1, 10)]
    for log in logs:
        if not log.is_file():
            continue
        try:
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        current: dict | None = None
        for line in lines:
            launch = _LAUNCH_RE.search(line)
            if launch:
                current = details.setdefault(launch.group("run"), {})
                current["pipeline"] = launch.group("pipeline")
                current["version"] = launch.group("branch") or launch.group("rev")
                continue
            if current is None or current.get("error"):
                continue
            error = _NF_ERROR_RE.search(line) or _NF_CAUSE_RE.search(line)
            if error:
                current["error"] = _clean_error(error.group("msg"))
    return details


def nextflow_runs(launch_dir: Path) -> list[PipelineRun]:
    history = launch_dir / ".nextflow" / "history"
    try:
        lines = history.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    details: dict[str, dict] | None = None
    runs: list[PipelineRun] = []
    for line in lines:
        cols = line.split("\t")
        if len(cols) < 7:
            continue
        when, duration, run_name, status, revision, _session, command = cols[:7]
        try:
            start = datetime.strptime(when.strip(), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        pipeline, cmd_revision, profile = _command_args(command)
        state = {"OK": "success", "ERR": "failed"}.get(status.strip(), "running")
        if details is None:
            details = _nextflow_log_details(launch_dir)
        extra = details.get(run_name.strip(), {})
        runs.append(PipelineRun(
            engine="nextflow",
            pipeline=redact_sensitive_text(extra.get("pipeline") or pipeline or launch_dir.name),
            status=state,
            start=start,
            minutes=parse_duration(duration) if duration.strip() != "-" else None,
            run_name=run_name.strip() or None,
            version=cmd_revision or extra.get("version") or (revision.strip()[:10] or None),
            profile=profile,
            error=extra.get("error") if state == "failed" else None,
            launch_dir=str(launch_dir),
        ))
    return runs


def snakemake_runs(launch_dir: Path) -> list[PipelineRun]:
    log_dir = launch_dir / ".snakemake" / "log"
    try:
        logs = sorted(log_dir.iterdir())
    except OSError:
        return []
    runs: list[PipelineRun] = []
    for log in logs:
        match = _SMK_LOG_RE.match(log.name)
        if match is None or not log.is_file():
            continue
        try:
            start = datetime.strptime(match.group("ts"), "%Y-%m-%dT%H%M%S")
            text = log.read_text(encoding="utf-8", errors="replace")
            end = datetime.fromtimestamp(log.stat().st_mtime)
        except (OSError, ValueError):
            continue
        error = None
        for line in text.splitlines():
            found = _SMK_ERROR_RE.match(line.strip())
            if found:
                error = f"Error in rule {found.group('rule')}" if found.group("rule") \
                    else found.group("other")
                break
        if error or "Exiting because a job execution failed" in text:
            status = "failed"
        elif "(100%) done" in text or "Nothing to be done" in text:
            status = "success"
        else:
            status = "incomplete"
        runs.append(PipelineRun(
            engine="snakemake",
            pipeline=redact_sensitive_text(launch_dir.name),
            status=status,
            start=start,
            minutes=max((end - start).total_seconds() / 60, 0.0),
            error=_clean_error(error) if error and status == "failed" else None,
            launch_dir=str(launch_dir),
        ))
    return runs


def is_launch_dir(path: Path) -> bool:
    return (path / ".nextflow" / "history").is_file() or (path / ".snakemake" / "log").is_dir()


def find_launch_dirs(root: Path, max_depth: int = MAX_SCAN_DEPTH) -> list[Path]:
    """Directories under `root` (inclusive, bounded depth) where a pipeline was launched."""
    found: list[Path] = []
    visited = 0
    frontier = [(root, 0)]
    while frontier and visited < MAX_SCAN_DIRS:
        path, depth = frontier.pop(0)
        visited += 1
        if is_launch_dir(path):
            found.append(path)
        if depth >= max_depth:
            continue
        try:
            with os.scandir(path) as entries:
                children = sorted(
                    Path(e.path) for e in entries
                    if e.is_dir(follow_symlinks=False) and e.name not in _SKIP_DIRS
                    and not e.name.startswith(".")
                )
        except OSError:
            continue
        frontier += [(child, depth + 1) for child in children]
    return found


def runs_in(launch_dir: Path) -> list[PipelineRun]:
    return nextflow_runs(launch_dir) + snakemake_runs(launch_dir)


def runs_on(runs: list[PipelineRun], day: date) -> list[PipelineRun]:
    return sorted((r for r in runs if r.start.date() == day), key=lambda r: r.start)


def run_thread(run: dict) -> str:
    """Open-thread text for a failed run (ticking it off in Obsidian closes it)."""
    label = run["pipeline"] + (f" `{run['run_name']}`" if run.get("run_name") else "")
    reason = f": {run['error']}" if run.get("error") else ""
    return f"Fix failed {run['engine']} run {label}{reason}"
