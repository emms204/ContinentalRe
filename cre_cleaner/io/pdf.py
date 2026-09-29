"""Convert PDF bordereaux to Excel via LlamaCloud Parse.

Uses LlamaParse with ``tables_as_spreadsheet`` so table pages land in a real
``.xlsx`` the existing cre_cleaner parsers can read. Requires
``LLAMA_CLOUD_API_KEY`` (or ``LLAMACLOUD_API_KEY``) in the environment.

Docs: https://developers.llamaindex.ai/llamaparse/parse/guides/configuring-parse/
"""
from __future__ import annotations

import os
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

PDF_SUFFIXES = {".pdf"}


@dataclass
class PdfConversion:
    source: Path
    output: Optional[Path] = None
    ok: bool = False
    detail: str = ""
    skipped: bool = False


@dataclass
class PdfBatchResult:
    conversions: List[PdfConversion] = field(default_factory=list)

    @property
    def excel_paths(self) -> List[Path]:
        return [c.output for c in self.conversions if c.ok and c.output is not None]

    @property
    def failures(self) -> List[PdfConversion]:
        return [c for c in self.conversions if not c.ok and not c.skipped]


def llama_cloud_api_key() -> Optional[str]:
    return (
        os.environ.get("LLAMA_CLOUD_API_KEY", "").strip()
        or os.environ.get("LLAMACLOUD_API_KEY", "").strip()
        or None
    )


def list_pdfs(raw_dir: Path) -> List[Path]:
    raw_dir = Path(raw_dir)
    if raw_dir.is_file():
        return [raw_dir] if raw_dir.suffix.lower() in PDF_SUFFIXES else []
    return sorted(
        p for p in raw_dir.rglob("*")
        if p.is_file()
        and p.suffix.lower() in PDF_SUFFIXES
        and not p.name.startswith("~$")
        and not p.name.startswith(".")
    )


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as resp, open(dest, "wb") as out:
        out.write(resp.read())


def convert_pdf_to_xlsx(
    pdf_path: Path,
    *,
    out_dir: Optional[Path] = None,
    api_key: Optional[str] = None,
    tier: str = "agentic",
) -> PdfConversion:
    """Parse one PDF and write sibling ``{stem}_llamaparse.xlsx`` (or out_dir)."""
    pdf_path = Path(pdf_path)
    key = api_key or llama_cloud_api_key()
    if not key:
        return PdfConversion(
            source=pdf_path,
            detail="LLAMA_CLOUD_API_KEY not set — cannot convert PDF",
        )
    if pdf_path.suffix.lower() not in PDF_SUFFIXES:
        return PdfConversion(source=pdf_path, detail="not a PDF", skipped=True)

    dest_dir = Path(out_dir) if out_dir else pdf_path.parent
    dest = dest_dir / f"{pdf_path.stem}_llamaparse.xlsx"
    if dest.exists() and dest.stat().st_size > 0:
        return PdfConversion(
            source=pdf_path, output=dest, ok=True,
            detail=f"reusing cached {dest.name}", skipped=True,
        )

    try:
        from llama_cloud import LlamaCloud
    except ImportError as e:
        return PdfConversion(
            source=pdf_path,
            detail=(
                "llama-cloud package not installed — "
                "pip/uv add 'llama-cloud>=2.8' "
                f"({e})"
            ),
        )

    try:
        client = LlamaCloud(api_key=key)
        file_obj = client.files.create(file=str(pdf_path), purpose="parse")
        result = client.parsing.parse(
            file_id=file_obj.id,
            tier=tier,
            version="latest",
            output_options={"tables_as_spreadsheet": {"enable": True}},
            expand=["xlsx_content_metadata"],
        )
        meta = getattr(result, "result_content_metadata", None) or getattr(
            result, "xlsx_content_metadata", None
        )
        # SDK shapes vary slightly across versions — walk common attributes.
        url = None
        if meta is not None:
            xlsx = getattr(meta, "xlsx", None) or meta
            if isinstance(xlsx, dict):
                url = xlsx.get("presigned_url") or xlsx.get("url")
            else:
                url = getattr(xlsx, "presigned_url", None) or getattr(xlsx, "url", None)
        if not url and hasattr(result, "model_dump"):
            dumped = result.model_dump()
            # nested walk
            stack = [dumped]
            while stack and not url:
                cur = stack.pop()
                if isinstance(cur, dict):
                    if "presigned_url" in cur and (
                        "xlsx" in str(cur).lower() or cur.get("exists") is True
                    ):
                        url = cur.get("presigned_url")
                    stack.extend(cur.values())
                elif isinstance(cur, list):
                    stack.extend(cur)
        if not url:
            return PdfConversion(
                source=pdf_path,
                detail="Parse finished but no XLSX presigned URL in result",
            )
        _download(url, dest)
        return PdfConversion(
            source=pdf_path,
            output=dest,
            ok=True,
            detail=f"wrote {dest.name}",
        )
    except Exception as e:
        return PdfConversion(source=pdf_path, detail=f"LlamaCloud parse failed: {e}")


def convert_pdfs_in_dir(
    raw_dir: Path,
    *,
    api_key: Optional[str] = None,
    paths: Optional[Sequence[Path]] = None,
) -> PdfBatchResult:
    """Convert every PDF under raw_dir (or an explicit list) to sibling xlsx."""
    batch = PdfBatchResult()
    pdfs = list(paths) if paths is not None else list_pdfs(raw_dir)
    for pdf in pdfs:
        batch.conversions.append(
            convert_pdf_to_xlsx(pdf, out_dir=pdf.parent, api_key=api_key)
        )
    return batch
