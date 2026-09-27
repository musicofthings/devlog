"""Classify a day's (or a project's) activity into coarse work types.

Deterministic keyword rules over the user's prompts, with a tool-mix fallback
when the prompts say nothing useful. Used to link Obsidian day notes to
work-type hub notes, so "every code review I did" is one click away.
"""

from __future__ import annotations

import re

# (slug, description, prompt pattern). Order is display priority.
WORK_TYPES: tuple[tuple[str, str, str], ...] = (
    (
        "code-review",
        "Reviewing, auditing, or assessing existing code.",
        r"\b(code[- ]?review|review(?:ed|ing)?|audit(?:ed|ing)?|assess)\b",
    ),
    (
        "planning",
        "Planning phases, brainstorming, designing, or proposing next steps.",
        r"\b(plan(?:ning)?|next phase|roadmap|brainstorm\w*|design doc|architect\w*|"
        r"suggest\w*|propos\w+|prd|spec)\b",
    ),
    (
        "bugfix",
        "Diagnosing and fixing defects, errors, and failures.",
        r"\b(fix\w*|bugs?|errors?|broken|crash\w*|fail\w*|not working|not behaving|"
        r"debug\w*|regression|struggling|issue)\b",
    ),
    (
        "feature",
        "Building new functionality.",
        r"\b(implement\w*|build\w*|built|add(?:ed|ing)?|creat\w+|wire\w*|support|new feature|"
        r"integrat\w+|enable)\b",
    ),
    (
        "refactor",
        "Restructuring or simplifying code without changing behavior.",
        r"\b(refactor\w*|clean ?up|simplif\w+|restructur\w+|renam\w+|reorganiz\w+|tidy)\b",
    ),
    (
        "testing",
        "Writing or running tests and evals.",
        r"\b(tests?|pytest|unit test|coverage|evals?|benchmark\w*)\b",
    ),
    (
        "docs",
        "Documentation, READMEs, and write-ups.",
        r"\b(readme|docs?|documentation|write-?up|changelog|explain the code)\b",
    ),
    (
        "ui-ux",
        "Interface, layout, and user-experience work.",
        r"\b(ui|ux|css|layout|slider|sidebar|sidecar|frontend|styling|theme|responsive)\b",
    ),
    (
        "git-ops",
        "Syncing, merging, branching, and repository housekeeping.",
        r"\b(git (?:sync|pull|push|merge|rebase)|merge\w*|branch\w*|rebase|pull request|"
        r"\bpr\b|commit\w*)\b",
    ),
    (
        "devops",
        "Environment, dependencies, CI, deployment, and machine upkeep.",
        r"\b(install\w*|dependenc\w+|deploy\w*|ci|docker|environment|disk space|"
        r"update\w*|upgrad\w+|mcp servers?|config\w*|scheduler|cache)\b",
    ),
    (
        "data-analysis",
        "Scientific and bioinformatics analysis (NGS, single-cell, pipelines).",
        r"\b(single[- ]cell|scrna|rna-?seq|transcriptom\w+|genom\w+|exome|vcf|bam|fastq|"
        r"variant\w*|sequencing|ngs|nextflow|snakemake|scanpy|seurat|bioinformatic\w*)\b",
    ),
    (
        "learning",
        "Learning, tutorials, and explanations.",
        r"\b(teach|learn\w*|tutorial|basics|understand|walk me through|explain)\b",
    ),
    (
        "research",
        "Literature and background research.",
        r"\b(research|literature|papers?|pubmed|survey|compare options)\b",
    ),
)

WORK_TYPE_DESCRIPTIONS = {slug: desc for slug, desc, _ in WORK_TYPES}
_COMPILED = tuple((slug, re.compile(pat, re.IGNORECASE)) for slug, _, pat in WORK_TYPES)
_ORDER = {slug: i for i, (slug, _, _) in enumerate(WORK_TYPES)}

_EDIT_TOOLS = {
    "edit", "write", "multiedit", "strreplace", "str_replace", "search_replace",
    "apply_patch", "create_file", "notebookedit",
}
_READ_TOOLS = {"read", "grep", "glob", "view", "read_file", "search", "ls", "codebase_search"}


def classify(texts: list[str], tool_calls: dict[str, int] | None = None) -> list[str]:
    """Return work-type slugs (priority order) for prompts + tool usage."""
    found: set[str] = set()
    for text in texts:
        for slug, pattern in _COMPILED:
            if pattern.search(text):
                found.add(slug)
    if not found and tool_calls:
        edits = sum(n for k, n in tool_calls.items() if k.lower() in _EDIT_TOOLS)
        reads = sum(n for k, n in tool_calls.items() if k.lower() in _READ_TOOLS)
        if edits:
            found.add("feature")
        elif reads:
            found.add("code-review")
    return sorted(found, key=_ORDER.__getitem__)


def describe(slug: str) -> str:
    return WORK_TYPE_DESCRIPTIONS.get(slug, "")
