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


def build_raw_digest(sessions: list[SessionDigest]) -> str:
    """Render a plain-text summary of a day's session digests."""
    if not sessions:
        return "No coding activity recorded today."
    lines: list[str] = []
    total_minutes = sum(s.duration_minutes for s in sessions)
    projects = sorted({s.project_path for s in sessions})
    lines.append(
        f"Total active time: {total_minutes:.0f} minutes across {len(sessions)} session(s)."
    )
    lines.append(f"Projects touched: {', '.join(projects)}")
    for s in sessions:
        lines.append(f"\n[Project: {s.project_path}, {s.duration_minutes:.0f} min, source={s.source}]")
        if s.user_messages:
            lines.append("  Tasks requested: " + " | ".join(s.user_messages))
        if s.tool_calls:
            tool_summary = ", ".join(f"{k} x{v}" for k, v in s.tool_calls.items())
            lines.append(f"  Tools used: {tool_summary}")
        if s.files_touched:
            lines.append(f"  Files touched: {', '.join(sorted(s.files_touched))}")
        if s.bash_commands:
            lines.append(f"  Commands run: {', '.join(s.bash_commands)}")
    return "\n".join(lines)
