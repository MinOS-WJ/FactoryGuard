from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_DOCS = (
    "docs/README.md",
    "docs/requirements/01-requirements-analysis.md",
    "docs/requirements/02-acceptance-criteria.md",
    "docs/design/01-architecture.md",
    "docs/design/02-data-and-config.md",
    "docs/design/03-ui-ux.md",
    "docs/testing/01-test-plan.md",
    "docs/testing/02-test-cases.md",
    "docs/testing/03-test-report.md",
    "docs/testing/04-blackbox-test-report.md",
    "docs/testing/05-whitebox-test-report.md",
    "docs/operations/01-deployment.md",
    "docs/operations/02-user-guide.md",
    "docs/operations/03-maintenance.md",
)


def test_required_documentation_files_exist():
    missing = [path for path in REQUIRED_DOCS if not (ROOT / path).is_file()]
    assert missing == []
    case_text = (ROOT / "docs/testing/02-test-cases.md").read_text(encoding="utf-8")
    case_ids = re.findall(r"^\| (TC-[A-Z]+-\d+) \|", case_text, flags=re.MULTILINE)
    duplicates = sorted({case_id for case_id in case_ids if case_ids.count(case_id) > 1})
    assert duplicates == []


def test_delivery_evidence_exists():
    """The delivery evidence must be present and reference real files."""
    evidence = ROOT / "docs/testing/evidence"
    assert evidence.is_dir(), "delivery evidence directory is missing"
    manifest = evidence / "manifest.json"
    assert manifest.is_file(), "evidence manifest is missing"
    import json

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    pages = payload.get("pages") or []
    assert len(pages) >= 6, "every primary page must have a screenshot"
    for page in pages:
        screenshot = evidence / str(page["screenshot"])
        assert screenshot.is_file(), f"missing screenshot {screenshot.name}"
        assert screenshot.stat().st_size > 10 * 1024, f"{screenshot.name} looks empty"
    recording = evidence / "console-walkthrough.mp4"
    assert recording.is_file(), "console walkthrough recording is missing"
    assert recording.stat().st_size > 50 * 1024, "the recording looks empty"


def test_local_markdown_links_resolve():
    broken: list[str] = []
    documents = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
    for document in documents:
        text = document.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            clean = target.split("#", 1)[0].strip()
            if not clean or clean.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (document.parent / clean).resolve()
            if not resolved.exists():
                broken.append(f"{document.relative_to(ROOT)} -> {target}")
    assert broken == []
