"""Detect topics (tools, libraries, languages, datasets) in a day's work.

Topics become hub notes in the vault (`DevLog/Topics/<slug>.md`) that link
every day and project where e.g. scanpy or Nextflow came up. Each topic hub
keeps a user-owned "Literature & notes" area, the natural place for Zotero
citekeys and paper links, so build logs connect to reading.

Matching is deterministic keyword search over prompts, commit subjects, open
threads, and file names. Users can add their own terms in config.toml:

    [topics]
    "CRISPR screens" = ["crispr", "sgrna", "mageck"]
"""

from __future__ import annotations

import re

# slug -> (display name, category, patterns). Patterns are regex fragments
# matched case-insensitively on word boundaries.
CATALOG: dict[str, tuple[str, str, tuple[str, ...]]] = {
    # --- genomics / bioinformatics
    "gatk": ("GATK", "bioinformatics", ("gatk", "haplotypecaller", "mutect2?", "bqsr")),
    "bwa": ("BWA", "bioinformatics", ("bwa(?:-mem2?)?",)),
    "samtools": ("samtools", "bioinformatics", ("samtools", "bcftools", "htslib")),
    "deepvariant": ("DeepVariant", "bioinformatics", ("deepvariant",)),
    "vep": ("VEP", "bioinformatics", ("vep", "ensembl vep", "variant effect predictor")),
    "annovar": ("ANNOVAR", "bioinformatics", ("annovar",)),
    "nextflow": ("Nextflow", "workflow", ("nextflow", "nf-core")),
    "snakemake": ("Snakemake", "workflow", ("snakemake", "snakefile")),
    "wdl": ("WDL / Cromwell", "workflow", ("wdl", "cromwell", "miniwdl")),
    "scanpy": ("scanpy", "single-cell", ("scanpy", "anndata", "h5ad")),
    "seurat": ("Seurat", "single-cell", ("seurat",)),
    "scvi-tools": ("scvi-tools", "single-cell", ("scvi(?:-tools)?", "scanvi")),
    "cellxgene": ("CELLxGENE", "single-cell", ("cellxgene", "cellxgene census")),
    "single-cell": ("single-cell RNA-seq", "single-cell",
                    ("single[- ]cell", "scrna(?:-?seq)?", "snrna(?:-?seq)?")),
    "rna-seq": ("RNA-seq", "genomics", ("rna-?seq", "deseq2", "edger", "salmon", "star aligner")),
    "variant-calling": ("variant calling", "genomics",
                        ("variant[- ]call\\w*", "vcf", "gvcf", "germline", "somatic")),
    "wgs-wes": ("WGS / WES", "genomics",
                ("wgs", "wes", "whole[- ]genome", "whole[- ]exome", "exome")),
    "ngs": ("NGS", "genomics", ("ngs", "next[- ]generation sequencing", "fastq", "bam", "cram")),
    "gnomad": ("gnomAD", "dataset", ("gnomad",)),
    "clinvar": ("ClinVar", "dataset", ("clinvar",)),
    "tcga": ("TCGA", "dataset", ("tcga",)),
    "acmg": ("ACMG classification", "clinical", ("acmg", "variant classification")),
    "hgvs": ("HGVS", "clinical", ("hgvs",)),
    "fhir": ("FHIR", "clinical", ("fhir", "hl7")),
    "pubmed": ("PubMed", "literature", ("pubmed", "pmid")),
    # --- ML / data
    "pytorch": ("PyTorch", "ml", ("pytorch", "torch")),
    "pandas": ("pandas", "data", ("pandas", "dataframe")),
    "polars": ("Polars", "data", ("polars",)),
    "numpy": ("NumPy", "data", ("numpy",)),
    "duckdb": ("DuckDB", "data", ("duckdb",)),
    "sqlite": ("SQLite", "data", ("sqlite",)),
    "postgres": ("PostgreSQL", "data", ("postgres(?:ql)?", "psql")),
    "jupyter": ("Jupyter", "data", ("jupyter", r"\.ipynb")),
    "llm": ("LLMs & agents", "ml",
            ("llms?", "anthropic api", "claude api", "openai", "agent sdk", "prompt engineering")),
    "mcp": ("MCP", "ml", ("mcp", "model context protocol")),
    # --- languages
    "python": ("Python", "language", ("python", r"\.py", "pytest", "pip")),
    "typescript": ("TypeScript", "language", ("typescript", r"\.tsx?")),
    "javascript": ("JavaScript", "language", ("javascript", r"\.jsx?", r"node\.?js", "npm")),
    "rust": ("Rust", "language", ("rust", r"\.rs", "cargo")),
    "r-lang": ("R", "language", (r"\.r", r"\.rmd", "tidyverse", "bioconductor", "rstudio")),
    "powershell": ("PowerShell", "language", ("powershell", r"\.ps1")),
    # --- app / infra
    "react": ("React", "frontend",
              ("reactjs", "react (?:app|components?|hooks?|native|router)", r"next\.js", "nextjs")),
    "tailwind": ("Tailwind CSS", "frontend", ("tailwind",)),
    "electron": ("Electron", "frontend", ("electron",)),
    "fastapi": ("FastAPI", "backend", ("fastapi",)),
    "docker": ("Docker", "infra", ("docker", "dockerfile", "docker compose")),
    "github-actions": ("GitHub Actions", "infra", ("github actions", "workflow_dispatch")),
    "github-pages": ("GitHub Pages", "infra", ("github pages", "gh-pages")),
    "obsidian": ("Obsidian", "tools", ("obsidian", "dataview")),
}


def _compile(patterns: tuple[str, ...]) -> re.Pattern[str]:
    """Whole-word match; file-extension patterns (`\\.py`) may follow a filename."""
    words = [p for p in patterns if not p.startswith(r"\.")]
    exts = [p for p in patterns if p.startswith(r"\.")]
    parts = []
    if words:
        parts.append(r"(?<![\w-])(?:" + "|".join(words) + r")(?![\w-])")
    if exts:
        parts.append(r"(?:" + "|".join(exts) + r")(?![\w-])")
    return re.compile("|".join(parts), re.IGNORECASE)


class TopicDetector:
    def __init__(self, extra: dict[str, list[str]] | None = None) -> None:
        self.names: dict[str, str] = {}
        self.categories: dict[str, str] = {}
        self._patterns: list[tuple[str, re.Pattern[str]]] = []
        for slug, (name, category, patterns) in CATALOG.items():
            self._add(slug, name, category, patterns)
        for name, terms in (extra or {}).items():
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "topic"
            patterns = tuple(re.escape(t) for t in (terms or [name]))
            self._add(slug, name, "custom", patterns)

    def _add(self, slug: str, name: str, category: str, patterns: tuple[str, ...]) -> None:
        self.names[slug] = name
        self.categories[slug] = category
        self._patterns = [(s, p) for s, p in self._patterns if s != slug]
        self._patterns.append((slug, _compile(patterns)))

    def detect(self, texts: list[str]) -> list[str]:
        blob = "\n".join(t for t in texts if t)
        if not blob:
            return []
        return sorted(slug for slug, pattern in self._patterns if pattern.search(blob))
