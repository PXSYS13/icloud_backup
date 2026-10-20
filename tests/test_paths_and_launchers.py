"""Проверка путей (корень диска, системные и домашняя папки — нельзя) и лаунчеров."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from utils import is_safe_dir, mask_apple_id, mask_token, sanitize_component

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("path", ["C:\\", "D:/", "/", "", "   ", "relative/dir", "C:\\Windows", "C:\\Windows\\Temp",
                                  "C:\\Program Files\\x", "c:\\programdata", "D:\\$Recycle.Bin", "\\\\?\\C:\\Windows\\x",
                                  "/etc", "/usr/local", "/var/log"])
def test_unsafe_dirs_rejected(path):
    ok, msg = is_safe_dir(path)
    assert not ok, path
    assert msg


def test_home_dir_rejected():
    ok, _ = is_safe_dir(os.path.expanduser("~"))
    assert not ok


def test_safe_dir_accepts_new_folder_under_existing_parent(tmp_path):
    ok, msg = is_safe_dir(str(tmp_path / "новая папка" / "фото"))
    assert ok, msg
    ok, msg = is_safe_dir(str(tmp_path / "нет"), must_exist=True)
    assert not ok


def test_sanitize_component():
    assert sanitize_component('a:b?c*d"e<f>g|h\\i/j') == "a_b_c_d_e_f_g_h_i_j"
    assert sanitize_component("CON") == "_CON"
    assert sanitize_component("name. ") == "name"
    assert sanitize_component("") == "_"


def test_masks_hide_secrets():
    assert mask_apple_id("ivan.petrov@icloud.com") == "iv…v@icloud.com"
    assert "petrov" not in mask_apple_id("ivan.petrov@icloud.com")
    tok = "y0_AgAAAABxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    masked = mask_token(tok)
    assert tok not in masked and masked.startswith("y0_Ag")


@pytest.mark.parametrize("name", ["webui.bat"])
def test_bat_files_are_crlf(name):
    data = (ROOT / name).read_bytes()
    assert data.count(b"\n") == data.count(b"\r\n"), f"{name}: cmd.exe нужен CRLF"
    text = data.decode("utf-8")
    assert "uv run webui.py" in text and "irm https://astral.sh/uv/install.ps1" in text
    assert ":uvfail" in text and ":done" in text


@pytest.mark.parametrize("name", ["webui.sh", "webui.command"])
def test_unix_launchers_are_lf_with_shebang(name):
    data = (ROOT / name).read_bytes()
    assert data.startswith(b"#!"), f"{name}: нет shebang"
    assert b"\r" not in data, f"{name}: CRLF сломает bash"


def test_mac_launcher_delegates_to_webui_sh():
    text = (ROOT / "webui.command").read_text(encoding="utf-8")
    assert 'cd "$(dirname "$0")"' in text and "webui.sh" in text


@pytest.mark.skipif(not shutil.which("git"), reason="нужен git")
@pytest.mark.parametrize("name", ["webui.sh", "webui.command"])
def test_unix_launchers_are_executable_in_git(name):
    out = subprocess.run(["git", "ls-files", "-s", name], cwd=ROOT, capture_output=True, text=True,
                         encoding="utf-8").stdout
    if not out:
        pytest.skip("файл ещё не в индексе git")
    assert out.split()[0] == "100755", out


def _working_bash() -> str | None:
    """bash, который правда запускается: на Windows в PATH бывает заглушка WSL без дистрибутива."""
    for cand in (shutil.which("bash"), r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"):
        if not cand or not os.path.exists(cand):
            continue
        try:
            out = subprocess.run([cand, "-c", "echo ok"], capture_output=True, text=True, encoding="utf-8", timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if out.returncode == 0 and out.stdout.strip() == "ok":
            return cand
    return None


def test_webui_sh_syntax():
    bash = _working_bash()
    if not bash:
        pytest.skip("нет рабочего bash")
    res = subprocess.run([bash, "-n", str(ROOT / "webui.sh")], capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, res.stderr
