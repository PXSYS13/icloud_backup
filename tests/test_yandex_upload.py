"""Загрузка на Яндекс Диск без сети: вместо yadisk.Client — FakeClient."""

from __future__ import annotations

import os
import threading
from types import SimpleNamespace

import pytest
import yadisk

import yandex_upload
from utils import YANDEX_ROOT, compute_md5, ru_bytes


class FakeClient:
    """Диск в памяти: пути файлов с их MD5, созданные папки, журнал загрузок.

    upload ничего не копирует — только записывает вызов и запоминает MD5 файла.
    upload_errors — исключения, которые upload бросит по очереди перед успехом.
    """

    def __init__(self, files: dict[str, str] | None = None, total: int = 10**12, used: int = 0,
                 login: str = "ivan", upload_errors: list | None = None,
                 disk_info_error: Exception | None = None):
        self.files = dict(files or {})
        self.dirs: set[str] = set()
        self.total = total
        self.used = used
        self.login = login
        self.upload_errors = list(upload_errors or [])
        self.disk_info_error = disk_info_error
        self.uploads: list[tuple[str, str, bool]] = []
        self.mkdirs: list[str] = []
        self.closed = False

    def get_disk_info(self):
        if self.disk_info_error is not None:
            raise self.disk_info_error
        return SimpleNamespace(total_space=self.total, used_space=self.used,
                               user=SimpleNamespace(login=self.login))

    def exists(self, path: str) -> bool:
        return path in self.files or path in self.dirs

    def get_meta(self, path: str):
        if path in self.files:
            return SimpleNamespace(md5=self.files[path], type="file")
        if path in self.dirs:
            return SimpleNamespace(md5=None, type="dir")
        raise yadisk.exceptions.PathNotFoundError("DiskNotFoundError", f"нет {path}")

    def mkdir(self, path: str):
        if path in self.dirs:
            raise yadisk.exceptions.DirectoryExistsError("DiskPathPointsToExistentDirectoryError", path)
        self.dirs.add(path)
        self.mkdirs.append(path)

    def upload(self, local: str, remote: str, overwrite: bool = False, **_kw):
        if self.upload_errors:
            raise self.upload_errors.pop(0)
        if remote in self.files and not overwrite:
            raise yadisk.exceptions.PathExistsError("DiskResourceAlreadyExistsError", remote)
        self.uploads.append((local, remote, overwrite))
        self.files[remote] = compute_md5(local)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def sleeps(monkeypatch) -> list:
    """Паузы между повторами записываются сюда, а не ждутся по-настоящему."""
    out: list = []
    monkeypatch.setattr(yandex_upload, "SLEEP", out.append)
    return out


def make_file(root, rel: str, data: bytes = b"photo") -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


REL = "2023/2023-07/IMG_0001.JPG"
REMOTE = f"{YANDEX_ROOT}/{REL}"


# ── plan_upload ──────────────────────────────────────────────────────────────


def test_plan_skips_service_dirs_and_non_media(tmp_path):
    keep = make_file(tmp_path, REL)
    keep_video = make_file(tmp_path, "2023/2023-08/IMG_0002.MOV", b"video")
    make_file(tmp_path, "_duplicates/IMG_0003.JPG")
    make_file(tmp_path, "_heic_originals/IMG_0004.HEIC")
    make_file(tmp_path, "2023/2023-07/IMG_0005.JPG.part")
    make_file(tmp_path, "2023/2023-07/IMG_0006.JPG.tmp")
    make_file(tmp_path, "state.db")
    make_file(tmp_path, "state.db-wal")
    make_file(tmp_path, "webui.log")
    make_file(tmp_path, "notes.txt")
    make_file(tmp_path, ".hidden.jpg")
    make_file(tmp_path, ".cache/IMG_0007.JPG")

    plan = yandex_upload.plan_upload(str(tmp_path))

    assert [f["rel"] for f in plan] == [REL, "2023/2023-08/IMG_0002.MOV"]
    assert plan[0]["local"] == keep and os.path.isabs(keep)
    assert plan[1]["local"] == keep_video
    assert plan[0]["size"] == len(b"photo")
    assert plan[1]["size"] == len(b"video")
    assert "\\" not in plan[0]["rel"]


def test_plan_empty_dir(tmp_path):
    assert yandex_upload.plan_upload(str(tmp_path)) == []


# ── upload_all ───────────────────────────────────────────────────────────────


def test_same_md5_is_skipped(tmp_path, sleeps):
    local = make_file(tmp_path, REL)
    cli = FakeClient(files={REMOTE: compute_md5(local)})

    res = yandex_upload.upload_all("tok", str(tmp_path), client=cli)

    assert res["ok"] is True
    assert (res["total"], res["done"], res["skipped"], res["failed"]) == (1, 0, 1, 0)
    assert cli.uploads == []
    assert cli.mkdirs == []
    assert res["bytes_total"] == 0 and res["bytes_done"] == 0
    assert res["remote_root"] == YANDEX_ROOT


def test_new_file_uploaded_with_parent_dirs(tmp_path, sleeps):
    local = make_file(tmp_path, REL)
    cli = FakeClient()
    seen: list[dict] = []

    res = yandex_upload.upload_all("tok", str(tmp_path), progress_cb=seen.append, client=cli)

    assert res["ok"] is True
    assert (res["done"], res["skipped"], res["failed"]) == (1, 0, 0)
    assert res["bytes_done"] == res["bytes_total"] == len(b"photo")
    assert cli.uploads == [(local, REMOTE, False)]
    assert cli.mkdirs == [YANDEX_ROOT, f"{YANDEX_ROOT}/2023", f"{YANDEX_ROOT}/2023/2023-07"]
    assert res["failed_files"] == []
    assert sleeps == []
    last = seen[-1]
    assert last["current"] == REL and last["done"] == 1 and last["stage"] == "upload"
    assert cli.closed is False  # чужой клиент не закрываем


def test_second_file_in_same_folder_does_not_recreate_dirs(tmp_path, sleeps):
    make_file(tmp_path, REL)
    make_file(tmp_path, "2023/2023-07/IMG_0002.JPG", b"other")
    cli = FakeClient()

    res = yandex_upload.upload_all("tok", str(tmp_path), client=cli)

    assert res["done"] == 2
    assert cli.mkdirs == [YANDEX_ROOT, f"{YANDEX_ROOT}/2023", f"{YANDEX_ROOT}/2023/2023-07"]


def test_existing_dirs_on_disk_are_not_an_error(tmp_path, sleeps):
    make_file(tmp_path, REL)
    cli = FakeClient()
    cli.dirs.update({YANDEX_ROOT, f"{YANDEX_ROOT}/2023"})

    res = yandex_upload.upload_all("tok", str(tmp_path), client=cli)

    assert res["done"] == 1 and res["failed"] == 0
    assert cli.mkdirs == [f"{YANDEX_ROOT}/2023/2023-07"]


def test_different_md5_gets_suffixed_name(tmp_path, sleeps):
    local = make_file(tmp_path, REL, b"new content")
    cli = FakeClient(files={REMOTE: "0" * 32})
    md5 = compute_md5(local)

    res = yandex_upload.upload_all("tok", str(tmp_path), client=cli)

    expected = f"{YANDEX_ROOT}/2023/2023-07/IMG_0001_{md5[:6]}.JPG"
    assert res["done"] == 1 and res["skipped"] == 0
    assert cli.uploads == [(local, expected, False)]
    assert cli.files[REMOTE] == "0" * 32  # старый файл на Диске не тронут


def test_suffixed_copy_already_on_disk_is_skipped(tmp_path, sleeps):
    local = make_file(tmp_path, REL, b"new content")
    md5 = compute_md5(local)
    alt = f"{YANDEX_ROOT}/2023/2023-07/IMG_0001_{md5[:6]}.JPG"
    cli = FakeClient(files={REMOTE: "0" * 32, alt: md5})

    res = yandex_upload.upload_all("tok", str(tmp_path), client=cli)

    assert res["skipped"] == 1 and res["done"] == 0
    assert cli.uploads == []


def test_not_enough_space_stops_before_upload(tmp_path, sleeps):
    make_file(tmp_path, REL, b"x" * 50)
    make_file(tmp_path, "2023/2023-07/IMG_0002.JPG", b"y" * 50)
    cli = FakeClient(total=1000, used=950)

    res = yandex_upload.upload_all("tok", str(tmp_path), client=cli)

    assert res["ok"] is False and res["kind"] == "space"
    assert cli.uploads == [] and cli.mkdirs == []
    assert ru_bytes(100) in res["msg"] and ru_bytes(50) in res["msg"]
    assert "мобильного приложения" in res["msg"]
    assert res["bytes_total"] == 100


def test_skipped_files_do_not_count_against_space(tmp_path, sleeps):
    local = make_file(tmp_path, REL, b"x" * 50)
    cli = FakeClient(files={REMOTE: compute_md5(local)}, total=1000, used=1000)

    res = yandex_upload.upload_all("tok", str(tmp_path), client=cli)

    assert res["ok"] is True and res["skipped"] == 1


def test_rate_limit_waits_once_then_retries(tmp_path, sleeps):
    local = make_file(tmp_path, REL)
    err = yadisk.exceptions.TooManyRequestsError("TooManyRequestsError", "429")
    cli = FakeClient(upload_errors=[err])
    seen: list[dict] = []

    res = yandex_upload.upload_all("tok", str(tmp_path), progress_cb=seen.append, client=cli)

    assert res["ok"] is True and res["done"] == 1 and res["failed"] == 0
    assert sleeps == [yandex_upload.RATE_LIMIT_WAIT_SEC]
    assert cli.uploads == [(local, REMOTE, False)]
    assert any(p["msg"] == yandex_upload.MSG_RATE_LIMIT for p in seen)


def test_connection_errors_retry_with_growing_pauses(tmp_path, sleeps):
    make_file(tmp_path, REL)
    errors = [yadisk.exceptions.YaDiskConnectionError("обрыв"),
              yadisk.exceptions.BadGatewayError("BadGateway", "502")]
    cli = FakeClient(upload_errors=errors)

    res = yandex_upload.upload_all("tok", str(tmp_path), client=cli)

    assert res["done"] == 1 and res["failed"] == 0
    assert sleeps == [5, 15]


def test_file_fails_after_retries_and_run_continues(tmp_path, sleeps):
    make_file(tmp_path, REL)
    make_file(tmp_path, "2023/2023-07/IMG_0002.JPG", b"other")
    errors = [yadisk.exceptions.UnavailableError("Unavailable", "503") for _ in range(4)]
    cli = FakeClient(upload_errors=errors)

    res = yandex_upload.upload_all("tok", str(tmp_path), client=cli)

    assert res["ok"] is True
    assert (res["done"], res["failed"]) == (1, 1)
    assert res["failed_files"] == [REL]
    assert sleeps == [5, 15, 45]
    assert [u[1] for u in cli.uploads] == [f"{YANDEX_ROOT}/2023/2023-07/IMG_0002.JPG"]


def test_unauthorized_on_account_check(tmp_path, sleeps):
    make_file(tmp_path, REL)
    err = yadisk.exceptions.UnauthorizedError("UnauthorizedError", "401")
    cli = FakeClient(disk_info_error=err)

    res = yandex_upload.upload_all("tok", str(tmp_path), client=cli)

    assert res["ok"] is False and res["kind"] == "auth"
    assert res["msg"] == yandex_upload.MSG_AUTH
    assert cli.uploads == []


def test_forbidden_during_upload_stops_with_auth(tmp_path, sleeps):
    make_file(tmp_path, REL)
    make_file(tmp_path, "2023/2023-07/IMG_0002.JPG", b"other")
    err = yadisk.exceptions.ForbiddenError("ForbiddenError", "403")
    cli = FakeClient(upload_errors=[err])

    res = yandex_upload.upload_all("tok", str(tmp_path), client=cli)

    assert res["ok"] is False and res["kind"] == "auth"
    assert cli.uploads == []  # первый файл не ушёл, до второго не дошли


def test_insufficient_storage_during_upload(tmp_path, sleeps):
    make_file(tmp_path, REL)
    err = yadisk.exceptions.InsufficientStorageError("InsufficientStorage", "507")
    cli = FakeClient(upload_errors=[err])

    res = yandex_upload.upload_all("tok", str(tmp_path), client=cli)

    assert res["ok"] is False and res["kind"] == "space"
    assert "мобильного приложения" in res["msg"]


def test_network_down_stops_run(tmp_path, sleeps):
    for i in range(yandex_upload.NETWORK_GIVE_UP + 2):
        make_file(tmp_path, f"2023/2023-07/IMG_{i:04d}.JPG", bytes([i]))
    tries = (len(yandex_upload.RETRY_PAUSES) + 1) * yandex_upload.NETWORK_GIVE_UP
    errors = [yadisk.exceptions.YaDiskConnectionError("обрыв") for _ in range(tries)]
    cli = FakeClient(upload_errors=errors)

    res = yandex_upload.upload_all("tok", str(tmp_path), client=cli)

    assert res["ok"] is False and res["kind"] == "network"
    assert res["failed"] == yandex_upload.NETWORK_GIVE_UP
    assert cli.uploads == []


def test_stop_event_stops_between_files(tmp_path, sleeps):
    make_file(tmp_path, REL)
    make_file(tmp_path, "2023/2023-07/IMG_0002.JPG", b"other")
    cli = FakeClient()
    stop = threading.Event()

    def on_progress(info: dict) -> None:
        if info["stage"] == "upload":
            stop.set()

    res = yandex_upload.upload_all("tok", str(tmp_path), progress_cb=on_progress,
                                   stop_event=stop, client=cli)

    assert res["ok"] is True and res["stopped"] is True
    assert res["done"] == 1 and len(cli.uploads) == 1


def test_unreadable_local_file_counts_as_failed(tmp_path, sleeps, monkeypatch):
    make_file(tmp_path, REL)
    cli = FakeClient()
    plan = yandex_upload.plan_upload(str(tmp_path))
    missing = dict(plan[0], rel="2023/2023-07/gone.JPG", local=str(tmp_path / "2023/2023-07/gone.JPG"))
    monkeypatch.setattr(yandex_upload, "plan_upload", lambda _dir: [missing, *plan])

    res = yandex_upload.upload_all("tok", str(tmp_path), client=cli)

    assert res["ok"] is True
    assert (res["done"], res["failed"]) == (1, 1)
    assert res["failed_files"] == [missing["rel"]]


# ── check_token ──────────────────────────────────────────────────────────────


def test_check_token_ok(monkeypatch):
    cli = FakeClient(total=2000, used=500, login="ivan")
    monkeypatch.setattr(yandex_upload, "make_client", lambda token: cli)

    res = yandex_upload.check_token("y0_secret_token_value")

    assert res["ok"] is True
    assert (res["login"], res["used"], res["total"], res["free"]) == ("ivan", 500, 2000, 1500)
    assert res["unreachable"] is False
    assert "ivan" in res["msg"]
    assert "secret" not in str(res)
    assert cli.closed is True


def test_check_token_rejected(monkeypatch):
    err = yadisk.exceptions.UnauthorizedError("UnauthorizedError", "401")
    cli = FakeClient(disk_info_error=err)
    monkeypatch.setattr(yandex_upload, "make_client", lambda token: cli)

    res = yandex_upload.check_token("y0_secret_token_value")

    assert res["ok"] is False and res["unreachable"] is False
    assert res["msg"] == yandex_upload.MSG_AUTH
    assert cli.closed is True


def test_check_token_unreachable(monkeypatch):
    err = yadisk.exceptions.YaDiskConnectionError("нет сети")
    cli = FakeClient(disk_info_error=err)
    monkeypatch.setattr(yandex_upload, "make_client", lambda token: cli)

    res = yandex_upload.check_token("y0_secret_token_value")

    assert res["ok"] is False and res["unreachable"] is True
    assert res["msg"] == yandex_upload.MSG_UNREACHABLE


def test_check_token_empty():
    res = yandex_upload.check_token("   ")
    assert res["ok"] is False and res["msg"] == yandex_upload.MSG_NO_TOKEN


def test_make_client_sets_timeout():
    cli = yandex_upload.make_client("y0_secret_token_value")
    try:
        assert cli.default_args["timeout"] == yandex_upload.DEFAULT_TIMEOUT
    finally:
        cli.close()
