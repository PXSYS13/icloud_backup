"""Инструкция и формы отзыва: картинки на месте, ссылки программы ведут на существующие формы."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / ".github" / "ISSUE_TEMPLATE"


def test_images_in_docs_exist():
    # Картинка с опечаткой в пути на GitHub молча не показывается — ловим здесь
    for doc in ("README.md", "INSTRUCTION.md"):
        text = (ROOT / doc).read_text(encoding="utf-8")
        images = re.findall(r"\((docs/[^)\s]+)\)|src=\"(docs/[^\"]+)\"", text)
        assert images, doc
        for md, html in images:
            assert (ROOT / (md or html)).is_file(), f"{doc}: нет файла {md or html}"


def test_status_links_point_to_issue_forms(client):
    links = client.get("/api/status").json()["links"]
    assert links["guide"].endswith("/blob/main/INSTRUCTION.md")
    assert (ROOT / "INSTRUCTION.md").is_file()
    for key in ("feedback", "bug_report"):
        name = links[key].rsplit("template=", 1)[1]
        assert (TEMPLATES / name).is_file(), f"{key}: нет формы {name}"
