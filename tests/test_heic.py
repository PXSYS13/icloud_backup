"""Конвертация HEIC в JPG: EXIF, mtime, режимы both/jpg/originals, повторный запуск."""

import os
import threading

import pillow_heif
import pytest
from PIL import Image, ImageCms

import heic_convert
from state import State
from utils import DIR_HEIC_ORIGINALS

pillow_heif.register_heif_opener()

DATE = "2023:07:15 12:34:56"
LAT = (55.0, 45.0, 20.88)
LON = (37.0, 37.0, 16.44)
MTIME_NS = 1_689_417_296 * 10**9  # 2023-07-15, кратно 100 нс — NTFS хранит точно


def exif_bytes(orientation=None) -> bytes:
    exif = Image.Exif()
    exif[0x010F] = "Apple"
    exif[0x0110] = "iPhone 13"
    exif[0x0132] = DATE
    if orientation is not None:
        exif[0x0112] = orientation
    ifd = exif.get_ifd(0x8769)
    ifd[0x9003] = DATE
    ifd[0x9004] = DATE
    gps = exif.get_ifd(0x8825)
    gps[1] = "N"
    gps[2] = LAT
    gps[3] = "E"
    gps[4] = LON
    return exif.tobytes()


def make_heic(path, orientation=None, size=(64, 32), icc=False) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img = Image.new("RGB", size, (200, 30, 30))
    kw = {}
    if icc:
        kw["icc_profile"] = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    img.save(path, format="HEIF", quality=80, exif=exif_bytes(orientation), **kw)
    os.utime(path, ns=(MTIME_NS, MTIME_NS))
    return str(path)


def make_jpg(path) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", (8, 8), (0, 0, 255)).save(path, format="JPEG")
    return str(path)


def register(db: State, asset_id: str, filename: str, kind: str, local_path: str,
             live_local_path: str | None = None) -> None:
    db.upsert_batch([{"id": asset_id, "filename": filename, "kind": kind, "size": 1,
                      "checksum": None, "created_ts": 1_689_417_296.0, "live_size": None,
                      "live_checksum": None, "live_filename": None}])
    db.mark_downloaded(asset_id, local_path, live_local_path, "md5")


@pytest.fixture
def db(tmp_path):
    state = State(str(tmp_path / "state.db"))
    yield state
    state.close()


@pytest.fixture
def library(tmp_path, db):
    """Папка скачивания: HEIC в 2023/2023-07, Live Photo (HEIC + MOV), обычный JPG."""
    root = tmp_path / "photos"
    month = root / "2023" / "2023-07"
    heic = make_heic(month / "IMG_0001.HEIC")
    live_heic = make_heic(month / "IMG_0002.HEIC")
    live_mov = month / "IMG_0002.MOV"
    live_mov.write_bytes(b"mov")
    plain = make_jpg(month / "IMG_0003.JPG")
    register(db, "a1", "IMG_0001.HEIC", "photo", heic)
    register(db, "a2", "IMG_0002.HEIC", "live", live_heic, str(live_mov))
    register(db, "a3", "IMG_0003.JPG", "photo", plain)
    return {"root": str(root), "month": month, "heic": heic, "live_heic": live_heic,
            "live_mov": str(live_mov), "plain": plain}


def read_exif(jpg: str):
    with Image.open(jpg) as img:
        exif = img.getexif()
        return dict(exif), dict(exif.get_ifd(0x8769)), dict(exif.get_ifd(0x8825)), img.size


def test_jpg_path_for():
    assert heic_convert.jpg_path_for(r"C:\x\IMG_0001.HEIC") == r"C:\x\IMG_0001.JPG"
    assert heic_convert.jpg_path_for("/x/photo.heic") == "/x/photo.jpg"
    assert heic_convert.jpg_path_for("/x/photo.HEIF") == "/x/photo.JPG"


def test_convert_keeps_exif_gps_and_icc(tmp_path):
    src = make_heic(tmp_path / "a.HEIC", icc=True)
    dst = str(tmp_path / "a.JPG")
    heic_convert.convert_heic_to_jpg(src, dst)
    assert os.path.getsize(dst) > 0
    assert not os.path.exists(dst + ".tmp")
    base, exif_ifd, gps, _ = read_exif(dst)
    assert base[0x010F] == "Apple"
    assert base[0x0110] == "iPhone 13"
    assert base[0x0132] == DATE
    assert exif_ifd[0x9003] == DATE
    assert exif_ifd[0x9004] == DATE
    assert gps[1] == "N" and gps[3] == "E"
    assert [float(v) for v in gps[2]] == pytest.approx(LAT)
    assert [float(v) for v in gps[4]] == pytest.approx(LON)
    with Image.open(dst) as img:
        assert img.info.get("icc_profile")


def test_convert_keeps_mtime(tmp_path):
    src = make_heic(tmp_path / "a.HEIC")
    dst = str(tmp_path / "a.JPG")
    heic_convert.convert_heic_to_jpg(src, dst)
    assert os.stat(dst).st_mtime_ns == os.stat(src).st_mtime_ns == MTIME_NS


def test_convert_resets_orientation_after_rotation(tmp_path):
    # pillow-heif уже повернул пиксели (64x32 → 32x64): в JPG тег поворота должен быть 1
    src = make_heic(tmp_path / "rot.HEIC", orientation=6)
    dst = str(tmp_path / "rot.JPG")
    heic_convert.convert_heic_to_jpg(src, dst)
    base, _, _, size = read_exif(dst)
    assert size == (32, 64)
    assert base[0x0112] == 1


def test_exif_reserialized_when_orientation_not_one(tmp_path):
    # Запасной путь: тег поворота не 1 (JPEG-плагин Pillow его не сбрасывает)
    src = tmp_path / "o.jpg"
    Image.new("RGB", (8, 8)).save(src, format="JPEG", exif=exif_bytes(orientation=6))
    with Image.open(src) as img:
        raw = heic_convert._exif_for_jpeg(img)
    exif = Image.Exif()
    exif.load(raw)
    assert exif[0x0112] == 1
    assert exif.get_ifd(0x8769)[0x9003] == DATE
    assert [float(v) for v in exif.get_ifd(0x8825)[2]] == pytest.approx(LAT)


def test_exif_too_long_drops_makernote(tmp_path):
    exif = Image.Exif()
    exif.load(exif_bytes())
    exif.get_ifd(0x8769)[0x927C] = b"\x01" * 70_000
    src = tmp_path / "big.HEIC"
    Image.new("RGB", (8, 8)).save(src, format="HEIF", quality=80, exif=exif.tobytes())
    dst = str(tmp_path / "big.JPG")
    heic_convert.convert_heic_to_jpg(str(src), dst)
    _, exif_ifd, gps, _ = read_exif(dst)
    assert 0x927C not in exif_ifd
    assert exif_ifd[0x9003] == DATE
    assert gps[1] == "N"


def test_long_paths(tmp_path):
    # Путь длиннее 240 символов: на Windows нужен префикс \\?\ (utils.to_long_path)
    deep = tmp_path
    while len(str(deep)) < 250:
        deep = deep / ("папка_" + "x" * 40)
    src = make_heic(deep / "IMG_0001.HEIC")
    dst = heic_convert.jpg_path_for(src)
    heic_convert.convert_heic_to_jpg(src, dst)
    assert read_exif(dst)[1][0x9003] == DATE
    assert os.stat(dst).st_mtime_ns == MTIME_NS


def test_mode_both_keeps_heic_and_db(db, library):
    res = heic_convert.process_photos(db, library["root"], "both")
    assert res == {"total": 2, "done": 2, "skipped": 0, "errors": 0, "mode": "both"}
    assert os.path.exists(library["heic"])
    assert os.path.exists(library["month"] / "IMG_0001.JPG")
    assert os.path.exists(library["month"] / "IMG_0002.JPG")
    assert os.path.exists(library["live_mov"])
    assert not os.path.exists(os.path.join(library["root"], DIR_HEIC_ORIGINALS))
    assert db.get("a1")["local_path"] == library["heic"]
    assert db.get("a2")["local_path"] == library["live_heic"]
    assert db.get("a2")["live_local_path"] == library["live_mov"]


def test_mode_jpg_moves_heic_and_updates_db(db, library):
    res = heic_convert.process_photos(db, library["root"], "jpg")
    assert res == {"total": 2, "done": 2, "skipped": 0, "errors": 0, "mode": "jpg"}
    jpg = str(library["month"] / "IMG_0001.JPG")
    moved = os.path.join(library["root"], DIR_HEIC_ORIGINALS, "2023", "2023-07", "IMG_0001.HEIC")
    assert os.path.exists(jpg)
    assert not os.path.exists(library["heic"])
    assert os.path.exists(moved)
    assert os.stat(moved).st_mtime_ns == MTIME_NS
    assert db.get("a1")["local_path"] == jpg
    # Live Photo: HEIC переехал, MOV на месте и в базе не менялся
    assert db.get("a2")["local_path"] == str(library["month"] / "IMG_0002.JPG")
    assert db.get("a2")["live_local_path"] == library["live_mov"]
    assert os.path.exists(library["live_mov"])
    assert db.get("a2")["status"] == "downloaded"


def test_second_run_skips_converted(db, library):
    heic_convert.process_photos(db, library["root"], "both")
    jpg = library["month"] / "IMG_0001.JPG"
    jpg.write_bytes(b"keep")  # метка: при повторе файл не должен перезаписаться
    res = heic_convert.process_photos(db, library["root"], "both")
    assert res == {"total": 2, "done": 0, "skipped": 2, "errors": 0, "mode": "both"}
    assert jpg.read_bytes() == b"keep"


def test_second_run_in_jpg_mode_finds_nothing(db, library):
    heic_convert.process_photos(db, library["root"], "jpg")
    res = heic_convert.process_photos(db, library["root"], "jpg")
    assert res == {"total": 0, "done": 0, "skipped": 0, "errors": 0, "mode": "jpg"}


def test_switch_from_both_to_jpg_moves_leftover_heic(db, library):
    heic_convert.process_photos(db, library["root"], "both")
    res = heic_convert.process_photos(db, library["root"], "jpg")
    assert res["done"] == 2 and res["errors"] == 0
    assert not os.path.exists(library["heic"])
    assert db.get("a1")["local_path"] == str(library["month"] / "IMG_0001.JPG")


def test_mode_originals_does_nothing(db, library):
    res = heic_convert.process_photos(db, library["root"], "originals")
    assert res == {"total": 0, "done": 0, "skipped": 0, "errors": 0, "mode": "originals"}
    assert not os.path.exists(library["month"] / "IMG_0001.JPG")
    assert db.get("a1")["local_path"] == library["heic"]


def test_unknown_mode_rejected(db, library):
    with pytest.raises(ValueError):
        heic_convert.process_photos(db, library["root"], "png")


def test_non_heic_untouched(db, library):
    before = os.stat(library["plain"])
    heic_convert.process_photos(db, library["root"], "jpg")
    after = os.stat(library["plain"])
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
    assert db.get("a3")["local_path"] == library["plain"]
    assert sorted(os.listdir(library["month"])) == [
        "IMG_0001.JPG", "IMG_0002.JPG", "IMG_0002.MOV", "IMG_0003.JPG"]


def test_name_taken_by_other_asset_gets_suffix(db, tmp_path):
    # IMG_0001.HEIC и IMG_0001.JPG — разные фото из iCloud: чужой JPG не затирается
    root = tmp_path / "photos"
    heic = make_heic(root / "IMG_0001.HEIC")
    other = make_jpg(root / "IMG_0001.JPG")
    other_before = os.stat(other)
    register(db, "h", "IMG_0001.HEIC", "photo", heic)
    register(db, "j", "IMG_0001.JPG", "photo", other)
    res = heic_convert.process_photos(db, str(root), "jpg")
    assert res["done"] == 1 and res["errors"] == 0
    assert os.stat(other).st_size == other_before.st_size
    new_path = db.get("h")["local_path"]
    assert new_path != other and new_path.endswith(".JPG") and os.path.exists(new_path)
    assert db.get("j")["local_path"] == other


def test_error_is_counted_and_processing_continues(db, library):
    register(db, "missing", "IMG_0009.HEIC", "photo", str(library["month"] / "IMG_0009.HEIC"))
    seen = []
    res = heic_convert.process_photos(db, library["root"], "both", progress_cb=seen.append)
    assert res == {"total": 3, "done": 2, "skipped": 0, "errors": 1, "mode": "both"}
    assert len(seen) == 3
    assert set(seen[-1]) == {"total", "done", "skipped", "errors", "current"}
    assert seen[-1]["errors"] == 1
    assert {s["current"] for s in seen} == {"IMG_0001.HEIC", "IMG_0002.HEIC", "IMG_0009.HEIC"}


def test_stop_event_stops_between_files(db, library):
    stop = threading.Event()
    res = heic_convert.process_photos(db, library["root"], "both",
                                      progress_cb=lambda info: stop.set(), stop_event=stop)
    assert res["total"] == 2 and res["done"] == 1
    assert res["skipped"] == 0 and res["errors"] == 0
