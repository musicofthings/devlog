from __future__ import annotations

from datetime import date, datetime, time, timedelta, tzinfo

from devlog.models import RawSession, SessionDigest


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

    Midnight-spanning sessions are split: events/time outside [day_start, day_end)
    are excluded, so calling this for consecutive days never double-counts.
    """
    day_start, day_end = day_bounds(target_date, tz)
    out: list[SessionDigest] = []
    for raw in sessions:
        # Normalize event times to tz for comparison
        events = [e for e in raw.events if day_start <= e.timestamp.astimezone(tz) < day_end]
        sess_start = raw.start_time.astimezone(tz)
        sess_end = raw.end_time.astimezone(tz)
        if sess_end <= day_start or sess_start >= day_end:
            continue
        clipped_start = max(sess_start, day_start)
        clipped_end = min(sess_end, day_end)
        if clipped_end <= clipped_start and not events:
            continue
        digest = SessionDigest(
            session_id=raw.session_id,
            project_path=raw.project_path,
            source=raw.source,
            start_time=clipped_start,
            end_time=clipped_end if clipped_end > clipped_start else clipped_start,
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
    total_minutes = sum(s.duration_minutes for s in sessions)
    if compact:
        projects = sorted({basename(s.project_path) for s in sessions})
        lines.append(f"{total_minutes:.0f} min, {len(sessions)} session(s): {', '.join(projects)}")
    else:
        projects = sorted({s.project_path for s in sessions})
        lines.append(
            f"Total active time: {total_minutes:.0f} minutes across {len(sessions)} session(s)."
        )
        lines.append(f"Projects touched: {', '.join(projects)}")

    for s in sessions:
        label = basename(s.project_path) if compact else s.project_path
        if compact:
            lines.append(f"\n[{label}, {s.duration_minutes:.0f}m, src={s.source}]")
        else:
            lines.append(
                f"\n[Project: {s.project_path}, {s.duration_minutes:.0f} min, source={s.source}]"
            )

        if s.user_messages:
            tasks = s.user_messages[:max_tasks] if max_tasks is not None else s.user_messages
            if task_len is not None:
                tasks = [_clip(t, task_len) for t in tasks]
            task_prefix = "  Tasks: " if compact else "  Tasks requested: "
            lines.append(task_prefix + " | ".join(tasks))

        if s.tool_calls:
            tool_summary = ", ".join(f"{k} x{v}" for k, v in s.tool_calls.items())
            lines.append(f"  Tools: {tool_summary}" if compact else f"  Tools used: {tool_summary}")

        if s.files_touched:
            files = sorted(s.files_touched)
            if max_files is not None:
                files = files[:max_files]
            if compact:
                files = [basename(f) for f in files]
            prefix = "  Files: " if compact else "  Files touched: "
            lines.append(prefix + ", ".join(files))

        if s.bash_commands:
            cmds = s.bash_commands[:max_cmds] if max_cmds is not None else s.bash_commands
            if cmd_len is not None:
                cmds = [_clip(c, cmd_len) for c in cmds]
            prefix = "  Cmds: " if compact else "  Commands run: "
            lines.append(prefix + ", ".join(cmds))

    return "\n".join(lines)
