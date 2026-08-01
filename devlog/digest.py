from __future__ import annotations

from datetime import date, datetime, time, timedelta, tzinfo

from devlog.models import RawSession, SessionDigest, SessionEvent
from devlog.privacy import redact_sensitive_text

ACTIVE_IDLE_CUTOFF = timedelta(minutes=30)


def day_bounds(target_date: date, tz: tzinfo) -> tuple[datetime, datetime]:
    """Return the [start, end) datetime bounds for target_date in tz."""
    start = datetime.combine(target_date, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    return start, end


def slice_for_date(
    sessions: list[RawSession],
    target_date: date,
    tz: tzinfo,
) -> list[SessionDigest]:
    """Clip each session to the portion overlapping target_date in tz.

    Midnight-spanning sessions are split and only dates with actual events are
    emitted. Active time is the union of intervals between nearby events; gaps
    longer than ACTIVE_IDLE_CUTOFF are treated as idle rather than work.
    """
    day_start, day_end = day_bounds(target_date, tz)
    out: list[SessionDigest] = []
    for raw in sessions:
        # Normalize event times to tz for comparison
        events = [e for e in raw.events if day_start <= e.timestamp.astimezone(tz) < day_end]
        if not events:
            continue
        sess_start = raw.start_time.astimezone(tz)
        sess_end = raw.end_time.astimezone(tz)
        clipped_start = max(sess_start, day_start)
        clipped_end = min(sess_end, day_end)
        intervals = _active_intervals_for_day(raw.events, day_start, day_end, tz)
        digest = SessionDigest(
            session_id=raw.session_id,
            project_path=raw.project_path,
            source=raw.source,
            start_time=clipped_start,
            end_time=clipped_end if clipped_end > clipped_start else clipped_start,
            active_minutes=sum(
                (end - start).total_seconds() / 60.0 for start, end in intervals
            ),
            active_intervals=intervals,
        )
        for e in events:
            if e.user_message:
                digest.user_messages.append(e.user_message)
            if e.tool_name:
                digest.tool_calls[e.tool_name] = digest.tool_calls.get(e.tool_name, 0) + 1
            if e.file_path:
                digest.files_touched.add(e.file_path)
            if e.bash_command:
                digest.bash_commands.append(e.bash_command)
            digest.tokens_in += e.tokens_in
            digest.tokens_out += e.tokens_out
            digest.tokens_cache_read += e.tokens_cache_read
        out.append(digest)
    return sorted(out, key=lambda s: s.start_time)


def _active_intervals_for_day(
    events: list[SessionEvent],
    day_start: datetime,
    day_end: datetime,
    tz: tzinfo,
) -> list[tuple[datetime, datetime]]:
    """Return non-idle event intervals clipped to one local calendar day."""
    timestamps = sorted({event.timestamp.astimezone(tz) for event in events})
    intervals: list[tuple[datetime, datetime]] = []
    for start, end in zip(timestamps, timestamps[1:], strict=False):
        gap = end - start
        if gap <= timedelta(0) or gap > ACTIVE_IDLE_CUTOFF:
            continue
        clipped_start = max(start, day_start)
        clipped_end = min(end, day_end)
        if clipped_end > clipped_start:
            intervals.append((clipped_start, clipped_end))
    return intervals


def total_active_minutes(sessions: list[SessionDigest]) -> float:
    """Return active minutes without double-counting overlapping sessions."""
    intervals: list[tuple[datetime, datetime]] = []
    standalone_minutes = 0.0
    for session in sessions:
        if session.active_intervals:
            intervals.extend(session.active_intervals)
        elif session.active_minutes is not None:
            standalone_minutes += session.duration_minutes
        elif session.end_time > session.start_time:
            intervals.append((session.start_time, session.end_time))

    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return standalone_minutes + sum(
        (end - start).total_seconds() / 60.0 for start, end in merged
    )


def basename(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").split("/")[-1] or path


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def build_raw_digest(sessions: list[SessionDigest], *, compact: bool = False) -> str:
    """Render a plain-text summary of a day's session digests.

    compact=True produces a shorter digest for LLM prompts (basenames, capped
    lists, clipped strings) to reduce input tokens without dropping the signal.
    """
    if not sessions:
        return "No coding activity recorded today."

    max_tasks = 2 if compact else None
    max_files = 5 if compact else None
    max_cmds = 3 if compact else None
    task_len = 120 if compact else None
    cmd_len = 80 if compact else None

    lines: list[str] = []
    total_minutes = total_active_minutes(sessions)
    if compact:
        projects = sorted(
            {redact_sensitive_text(basename(s.project_path)) for s in sessions}
        )
        lines.append(f"{total_minutes:.0f} min, {len(sessions)} session(s): {', '.join(projects)}")
    else:
        projects = sorted({redact_sensitive_text(s.project_path) for s in sessions})
        lines.append(
            f"Total active time: {total_minutes:.0f} minutes across {len(sessions)} session(s)."
        )
        lines.append(f"Projects touched: {', '.join(projects)}")

    for s in sessions:
        label = (
            redact_sensitive_text(basename(s.project_path))
            if compact
            else redact_sensitive_text(s.project_path)
        )
        if compact:
            lines.append(f"\n[{label}, {s.duration_minutes:.0f}m, src={s.source}]")
        else:
            lines.append(
                f"\n[Project: {label}, {s.duration_minutes:.0f} min, source={s.source}]"
            )

        if s.user_messages:
            tasks = s.user_messages[:max_tasks] if max_tasks is not None else s.user_messages
            tasks = [redact_sensitive_text(task) for task in tasks]
            if task_len is not None:
                tasks = [_clip(t, task_len) for t in tasks]
            task_prefix = "  Tasks: " if compact else "  Tasks requested: "
            lines.append(task_prefix + " | ".join(tasks))

        if s.tool_calls:
            tool_summary = ", ".join(
                f"{redact_sensitive_text(k)} x{v}" for k, v in s.tool_calls.items()
            )
            lines.append(f"  Tools: {tool_summary}" if compact else f"  Tools used: {tool_summary}")

        if s.files_touched:
            files = sorted(s.files_touched)
            if max_files is not None:
                files = files[:max_files]
            if compact:
                files = [basename(f) for f in files]
            files = [redact_sensitive_text(file) for file in files]
            prefix = "  Files: " if compact else "  Files touched: "
            lines.append(prefix + ", ".join(files))

        if s.bash_commands:
            cmds = s.bash_commands[:max_cmds] if max_cmds is not None else s.bash_commands
            cmds = [redact_sensitive_text(cmd) for cmd in cmds]
            if cmd_len is not None:
                cmds = [_clip(c, cmd_len) for c in cmds]
            prefix = "  Cmds: " if compact else "  Commands run: "
            lines.append(prefix + ", ".join(cmds))

    return "\n".join(lines)
