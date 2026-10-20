"""Дубли по MD5 уезжают в _duplicates (не удаляются), раскладка по датам переносит файлы и помнит пути."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import dedupe
import organize
from state import DUPLICATE_MARKER, State
from utils import DIR_DUP, compute_md5


def seed(tmp_path: Path, by_month: bool = False) -> tuple[State, Path, dict]:
    root = tmp_path / "dl"
    state = State(str(tmp_path / "state.db"))
    files = {}
    same = os.urandom(5000)
    spec = [
        ("a", "IMG_0001.JPG", same, datetime(2021, 3, 1)),
        ("b", "IMG_0002.JPG", same, datetime(2022, 7, 9)),       # дубль «a», позже по дате
        ("c", "IMG_0003.JPG", os.urandom(4000), datetime(2022, 7, 10)),
        ("d", "IMG_0004.HEIC", same, datetime(2020, 1, 1)),      # дубль, но самый ранний — остаётся он
    ]
    rows = []
    for aid, name, data, dt in spec:
        folder = root / f"{dt.year}/{dt.year}-{dt.month:02d}" if by_month else root
        folder.mkdir(parents=True, exist_ok=True)
        p = folder / name
        p.write_bytes(data)
        files[aid] = p
        rows.append({"id": aid, "filename": name, "kind": "photo", "size": len(data), "checksum": aid,
                     "created_ts": dt.timestamp()})
    state.upsert_batch(rows)
    for aid, p in files.items():
        state.mark_downloaded(aid, str(p), None, compute_md5(str(p)))
    return state, root, files


def test_duplicates_move_to_duplicates_folder_and_keep_earliest(tmp_path):
    state, root, files = seed(tmp_path)
    res = dedupe.move_duplicates(state, str(root))
    assert res["moved"] == 2 and res["groups"] == 1
    # Самый ранний (d, 2020) остался на месте, a и b переехали
    assert files["d"].exists() and files["c"].exists()
    assert not files["a"].exists() and not files["b"].exists()
    for aid in ("a", "b"):
        row = state.get(aid)
        assert row["status"] == "downloaded"
        assert row["error"].startswith(DUPLICATE_MARKER) and row["error"].endswith("d")
        assert Path(row["local_path"]).exists()
        assert Path(row["local_path"]).parent == root / DIR_DUP
    assert sum(1 for _ in (root / DIR_DUP).iterdir()) == 2
    assert state.get_stats()["duplicates"] == 2
    # Повторный запуск ничего не трогает
    res2 = dedupe.move_duplicates(state, str(root))
    assert res2["moved"] == 0


def test_relayout_by_month_and_back(tmp_path):
    state, root, files = seed(tmp_path, by_month=False)
    res = organize.relayout(state, str(root), by_month=True)
    assert res["moved"] == 4 and res["errors"] == 0
    for aid, dt in (("a", "2021/2021-03"), ("b", "2022/2022-07"), ("c", "2022/2022-07"), ("d", "2020/2020-01")):
        row = state.get(aid)
        assert Path(row["local_path"]).exists()
        assert Path(row["local_path"]).parent == root / Path(dt)
    # Обратно в одну папку: опустевшие папки по датам убираются
    res = organize.relayout(state, str(root), by_month=False)
    assert res["moved"] == 4
    assert {p.name for p in root.iterdir()} == {"IMG_0001.JPG", "IMG_0002.JPG", "IMG_0003.JPG", "IMG_0004.HEIC"}


def test_relayout_skips_duplicates_folder(tmp_path):
    state, root, files = seed(tmp_path)
    dedupe.move_duplicates(state, str(root))
    organize.relayout(state, str(root), by_month=True)
    assert (root / DIR_DUP).exists()
    assert sum(1 for _ in (root / DIR_DUP).iterdir()) == 2


def test_cli_get_date_prefers_exif_then_name_then_mtime(tmp_path):
    p = tmp_path / "2019-08-07 photo.jpg"
    p.write_bytes(b"not an image")
    assert organize.get_date(str(p)) == (2019, 8)
    q = tmp_path / "noname.bin"
    q.write_bytes(b"x")
    os.utime(q, (datetime(2015, 2, 3).timestamp(),) * 2)
    assert organize.get_date(str(q)) == (2015, 2)
