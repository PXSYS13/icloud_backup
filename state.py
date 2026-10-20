"""SQLite-трекер медиатеки и скачивания (state.db).

Каждый файл из iCloud Фото регистрируется в таблице `assets` по идентификатору iCloud
с одним из статусов: pending / downloaded / failed / skipped. Благодаря базе программа
продолжает с того же места после закрытия окна, обрыва сети или перезагрузки.
WAL-режим позволяет читать из интерфейса, пока рабочий поток пишет.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from utils import file_size_safe, path_key, sanitize_component, short_id, sqlite_tx, to_long_path, with_suffix_before_ext


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    kind TEXT NOT NULL,
    size INTEGER,
    checksum TEXT,
    created_ts REAL,
    live_size INTEGER,
    live_checksum TEXT,
    live_filename TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    local_path TEXT,
    live_local_path TEXT,
    md5 TEXT,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    downloaded_at TEXT,
    seen_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_assets_status ON assets(status);
CREATE INDEX IF NOT EXISTS idx_assets_md5 ON assets(md5);
CREATE INDEX IF NOT EXISTS idx_assets_local ON assets(local_path);
-- Служебные записи: когда собирали список, под какой аккаунт
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

KINDS = ("photo", "video", "live")
STATUSES = ("pending", "downloaded", "failed", "skipped")

# Маркер в колонке error: файл признан дублем и перенесён в _duplicates (обратимо)
DUPLICATE_MARKER = "duplicate | "


class State:
    """Тонкая обёртка над SQLite: по соединению на поток (через threading.local)."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._tls = threading.local()
        self._lock = threading.Lock()
        self._all_conns: list[sqlite3.Connection] = []
        self._all_conns_lock = threading.Lock()
        self._init_schema()

    # ── низкоуровневое ────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._tls, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None,
                                   check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.row_factory = sqlite3.Row
            self._tls.conn = conn
            with self._all_conns_lock:
                self._all_conns.append(conn)
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            self._conn().executescript(SCHEMA)

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with sqlite_tx(self._conn()) as conn:
            yield conn

    # ── служебные значения ───────────────────────────────────────────

    def get_meta(self, key: str) -> str | None:
        row = self._conn().execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str | None) -> None:
        with self._tx() as conn:
            if value is None:
                conn.execute("DELETE FROM meta WHERE key = ?", (key,))
            else:
                conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) "
                             "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    # ── регистрация списка ───────────────────────────────────────────

    def upsert_batch(self, items: Iterable[dict], seen_at: str | None = None) -> None:
        """Пакетная регистрация файлов из списка. Уже скачанные не трогает.

        Каждый элемент — словарь с ключами id, filename, kind, size, checksum, created_ts,
        live_size, live_checksum, live_filename. Если у скачанного файла в iCloud сменилась
        контрольная сумма (это другая версия оригинала), он возвращается в очередь.
        """
        seen = seen_at or _utc_now()
        rows = [(i["id"], i["filename"], i["kind"], i.get("size"), i.get("checksum"),
                 i.get("created_ts"), i.get("live_size"), i.get("live_checksum"),
                 i.get("live_filename"), seen) for i in items]
        if not rows:
            return
        with self._tx() as conn:
            conn.executemany(
                """
                INSERT INTO assets (id, filename, kind, size, checksum, created_ts,
                                    live_size, live_checksum, live_filename, status, seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                ON CONFLICT(id) DO UPDATE SET
                    filename = excluded.filename,
                    kind = excluded.kind,
                    size = excluded.size,
                    created_ts = COALESCE(excluded.created_ts, assets.created_ts),
                    live_size = excluded.live_size,
                    live_checksum = excluded.live_checksum,
                    live_filename = excluded.live_filename,
                    seen_at = excluded.seen_at,
                    checksum = excluded.checksum,
                    status = CASE
                        WHEN assets.status = 'downloaded'
                             AND excluded.checksum IS NOT NULL AND assets.checksum IS NOT NULL
                             AND excluded.checksum <> assets.checksum
                        THEN 'pending'
                        ELSE assets.status
                    END
                """,
                rows,
            )

    def mark_downloaded(self, asset_id: str, local_path: str, live_local_path: str | None,
                        md5: str | None) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE assets SET status='downloaded', local_path=?, live_local_path=?, md5=?, "
                "error=NULL, downloaded_at=? WHERE id=?",
                (local_path, live_local_path, md5, _utc_now(), asset_id),
            )

    def mark_failed(self, asset_id: str, error: str) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE assets SET status='failed', error=?, attempts=attempts+1 WHERE id=?",
                         (error[:2000], asset_id))

    def bump_attempt(self, asset_id: str, error: str) -> int:
        """Неудачная попытка: файл остаётся в очереди. Возвращает число попыток."""
        with self._tx() as conn:
            conn.execute("UPDATE assets SET error=?, attempts=attempts+1 WHERE id=?",
                         (error[:2000], asset_id))
            row = conn.execute("SELECT attempts FROM assets WHERE id=?", (asset_id,)).fetchone()
            return int(row["attempts"]) if row else 0

    def mark_skipped(self, asset_id: str, reason: str) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE assets SET status='skipped', error=? WHERE id=?",
                         (reason[:2000], asset_id))

    def reset_failed_to_pending(self) -> int:
        with self._tx() as conn:
            cur = conn.execute("UPDATE assets SET status='pending', error=NULL, attempts=0 "
                               "WHERE status='failed'")
            return cur.rowcount

    def set_local_paths(self, asset_id: str, local_path: str, live_local_path: str | None) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE assets SET local_path=?, live_local_path=? WHERE id=?",
                         (local_path, live_local_path, asset_id))

    def set_md5(self, asset_id: str, md5: str | None) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE assets SET md5=? WHERE id=?", (md5, asset_id))

    def move_to_duplicates(self, asset_id: str, new_path: str, keep_id: str) -> None:
        """Файл перенесён в _duplicates: помечаем, но не забываем, что он скачан."""
        with self._tx() as conn:
            conn.execute("UPDATE assets SET local_path=?, error=? WHERE id=?",
                         (new_path, f"{DUPLICATE_MARKER}{keep_id}", asset_id))

    def update_local_path(self, asset_id: str, local_path: str | None = None,
                          live_local_path: str | None = None) -> None:
        """Файл переехал (раскладка, конвертация): запоминаем новое место."""
        with self._tx() as conn:
            if local_path is not None:
                conn.execute("UPDATE assets SET local_path=? WHERE id=?", (local_path, asset_id))
            if live_local_path is not None:
                conn.execute("UPDATE assets SET live_local_path=? WHERE id=?", (live_local_path, asset_id))

    # ── чтение ────────────────────────────────────────────────────────

    def get(self, asset_id: str) -> sqlite3.Row | None:
        return self._conn().execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()

    def status_of(self, asset_id: str) -> str | None:
        row = self._conn().execute("SELECT status FROM assets WHERE id=?", (asset_id,)).fetchone()
        return row["status"] if row else None

    def get_pending(self) -> Iterable[sqlite3.Row]:
        return self._conn().execute("SELECT * FROM assets WHERE status='pending' ORDER BY created_ts ASC")

    def get_downloaded(self) -> Iterable[sqlite3.Row]:
        return self._conn().execute(
            "SELECT * FROM assets WHERE status='downloaded' AND local_path IS NOT NULL ORDER BY created_ts ASC")

    def get_failed(self, limit: int = 200) -> list[sqlite3.Row]:
        return self._conn().execute(
            "SELECT id, filename, kind, size, error FROM assets WHERE status='failed' LIMIT ?", (limit,)).fetchall()

    def recent_downloaded(self, limit: int = 8) -> list[dict]:
        rows = self._conn().execute(
            "SELECT filename, kind, size, downloaded_at FROM assets WHERE status='downloaded' "
            "ORDER BY downloaded_at DESC LIMIT ?", (limit,)).fetchall()
        return [{"name": r["filename"], "kind": r["kind"], "size": r["size"] or 0,
                 "at": _utc_ts(r["downloaded_at"])} for r in rows]

    def is_path_taken(self, local_path: str, except_id: str) -> bool:
        key = path_key(local_path)
        rows = self._conn().execute(
            "SELECT id, local_path, live_local_path FROM assets WHERE id <> ? AND "
            "(local_path IS NOT NULL OR live_local_path IS NOT NULL)", (except_id,))
        for r in rows:
            for p in (r["local_path"], r["live_local_path"]):
                if p and path_key(p) == key:
                    return True
        return False

    def get_stats(self) -> dict:
        """Сводка для интерфейса: по статусам, по видам, по годам, байты."""
        conn = self._conn()
        out = {
            "total": 0, "pending": 0, "downloaded": 0, "failed": 0, "skipped": 0,
            "bytes_total": 0, "bytes_done": 0, "bytes_pending": 0, "bytes_failed": 0,
            "photos": 0, "videos": 0, "live": 0,
            "bytes_photos": 0, "bytes_videos": 0, "bytes_live": 0,
            "years": [], "duplicates": 0,
            "listed_at": self.get_meta("listed_at"),
        }
        for r in conn.execute(
            "SELECT status, kind, COUNT(*) AS n, "
            "COALESCE(SUM(COALESCE(size,0) + COALESCE(live_size,0)),0) AS b FROM assets "
            "GROUP BY status, kind"):
            st, kind, n, b = r["status"], r["kind"], int(r["n"]), int(r["b"])
            out[st] = out.get(st, 0) + n
            out["total"] += n
            out["bytes_total"] += b
            if st == "downloaded":
                out["bytes_done"] += b
            elif st == "pending":
                out["bytes_pending"] += b
            elif st == "failed":
                out["bytes_failed"] += b
            key = {"photo": "photos", "video": "videos", "live": "live"}.get(kind)
            if key:
                out[key] += n
                out[f"bytes_{key}"] += b
        years = conn.execute(
            "SELECT CAST(strftime('%Y', created_ts, 'unixepoch') AS INTEGER) AS y, "
            "COUNT(*) AS n, COALESCE(SUM(COALESCE(size,0) + COALESCE(live_size,0)),0) AS b, "
            "SUM(status='downloaded') AS done FROM assets WHERE created_ts IS NOT NULL "
            "GROUP BY y ORDER BY y DESC").fetchall()
        out["years"] = [{"year": r["y"], "count": int(r["n"]), "bytes": int(r["b"]),
                         "done": int(r["done"] or 0)} for r in years if r["y"]]
        dup = conn.execute("SELECT COUNT(*) AS n FROM assets WHERE error LIKE ?",
                           (DUPLICATE_MARKER + "%",)).fetchone()
        out["duplicates"] = int(dup["n"]) if dup else 0
        return out

    def last_downloaded_at(self) -> float | None:
        row = self._conn().execute(
            "SELECT MAX(downloaded_at) AS at FROM assets WHERE status='downloaded'").fetchone()
        return _utc_ts(row["at"]) if row else None

    def duplicate_groups(self) -> list[list[sqlite3.Row]]:
        """Группы скачанных файлов с одинаковыми MD5 и размером (минимум два в группе)."""
        rows = self._conn().execute(
            "SELECT id, filename, size, md5, local_path, live_local_path, created_ts, error FROM assets "
            "WHERE status='downloaded' AND md5 IS NOT NULL AND local_path IS NOT NULL "
            "AND COALESCE(size,0) > 0 ORDER BY md5, created_ts, id").fetchall()
        groups: dict[tuple[str, int], list[sqlite3.Row]] = {}
        for r in rows:
            groups.setdefault((r["md5"], int(r["size"])), []).append(r)
        return [g for g in groups.values() if len(g) > 1]

    def __enter__(self) -> State:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        with self._all_conns_lock:
            conns = list(self._all_conns)
            self._all_conns.clear()
        for conn in conns:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        self._tls.conn = None


def _utc_ts(value) -> float | None:
    if not value:
        return None
    try:
        import calendar
        return float(calendar.timegm(time.strptime(str(value)[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")))
    except ValueError:
        return None


# ── Куда класть файл ────────────────────────────────────────────────────────


def month_folder(created_ts: float | None) -> str:
    """«2023/2023-07» по дате съёмки; без даты — «без даты»."""
    if not created_ts:
        return "без даты"
    t = time.localtime(created_ts)
    return os.path.join(str(t.tm_year), f"{t.tm_year}-{t.tm_mon:02d}")


def plan_local_paths(state: State, asset: sqlite3.Row, download_dir: str, by_month: bool) -> tuple[str, str | None]:
    """Назначает локальные пути файлу и его видеочасти (Live Photo).

    Имя файла чистится от запрещённых символов. Если путь уже занят другим файлом
    (в iCloud бывают одноимённые IMG_0001.HEIC из разных лет), к имени добавляется
    короткий хвост из идентификатора. Уже назначенный путь внутри папки не меняется.
    """
    root = os.path.abspath(download_dir)
    folder = os.path.join(root, month_folder(asset["created_ts"])) if by_month else root
    name = sanitize_component(asset["filename"])
    live_name = sanitize_component(asset["live_filename"]) if asset["live_filename"] else None

    current = asset["local_path"]
    if current and _under(current, root):
        return current, asset["live_local_path"]

    local = os.path.join(folder, name)
    live = os.path.join(folder, live_name) if live_name else None
    taken = state.is_path_taken(local, asset["id"]) or (live is not None and state.is_path_taken(live, asset["id"]))
    if not taken:
        # Файл с таким именем уже лежит на диске, но это не наш: не затирать чужое
        existing = file_size_safe(local)
        if existing is not None and asset["size"] and existing != asset["size"]:
            taken = True
    if taken:
        tail = "_" + short_id(asset["id"])
        local = with_suffix_before_ext(local, tail)
        live = with_suffix_before_ext(live, tail) if live else None
    return local, live


def _under(path: str, root: str) -> bool:
    try:
        p = os.path.normcase(os.path.abspath(path))
        r = os.path.normcase(os.path.abspath(root)).rstrip("\\/")
        return p == r or p.startswith(r + os.sep)
    except ValueError:
        return False


def local_copy_matches(local_path: str, size: int | None) -> bool:
    """Докачка: на месте уже лежит файл того же размера."""
    existing = file_size_safe(local_path)
    return existing is not None and size is not None and existing == size


def ensure_parent(path: str) -> None:
    os.makedirs(to_long_path(os.path.dirname(path)), exist_ok=True)
