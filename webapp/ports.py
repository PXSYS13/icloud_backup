"""Где работать и кого пускать: выбор порта при запуске и защита от чужих сайтов.

Если 8000 держит чужая программа, берём следующий свободный порт. Если там уже работает
копия этой программы из этой же папки, вторую не запускаем: две копии мешали бы друг
другу в одной базе.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import socket
import time

from fastapi.responses import JSONResponse

from utils import IS_WINDOWS
from webapp import paths

log = logging.getLogger("webui")

HOST = "127.0.0.1"
APP_ID = "icloud-backup"
PORT_TRIES = 20
NO_BROWSER_ENV = "ICLOUD_BK_NO_BROWSER"


def _port_from_env() -> int:
    try:
        port = int(os.environ.get("ICLOUD_BK_PORT") or 8000)
    except ValueError:
        return 8000
    return port if 1024 <= port <= 65535 else 8000


PORT = _port_from_env()
PORT_FIXED = bool((os.environ.get("ICLOUD_BK_PORT") or "").strip())

_ALLOWED_ORIGINS = {f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}"}
_ALLOWED_HOSTS = {f"localhost:{PORT}", f"127.0.0.1:{PORT}", "localhost", "127.0.0.1"}


def use_port(port: int) -> None:
    global PORT, _ALLOWED_ORIGINS, _ALLOWED_HOSTS
    PORT = port
    _ALLOWED_ORIGINS = {f"http://localhost:{port}", f"http://127.0.0.1:{port}"}
    _ALLOWED_HOSTS = {f"localhost:{port}", f"127.0.0.1:{port}", "localhost", "127.0.0.1"}


async def csrf_guard(request, call_next):
    """Блокирует запросы с чужих сайтов (CSRF) и DNS-rebinding.

    Host проверяется для ВСЕХ методов, иначе чужая страница, перепривязанная на
    127.0.0.1, прочитала бы /api/status. Origin — для методов, которые что-то меняют.
    """
    host = (request.headers.get("host") or "").lower()
    if host and host not in _ALLOWED_HOSTS:
        return JSONResponse({"detail": "Forbidden host"}, status_code=403)
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        origin = request.headers.get("origin") or ""
        if origin and origin not in _ALLOWED_ORIGINS:
            return JSONResponse({"detail": "Forbidden origin"}, status_code=403)
    return await call_next(request)


_ADDR_IN_USE = {errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", errno.EADDRINUSE)}


def bind_port(port: int) -> socket.socket | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if not IS_WINDOWS:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((HOST, port))
    except OSError:
        sock.close()
        return None
    return sock


def _ipv6_port_taken(port: int) -> bool:
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    except OSError:
        return False
    try:
        if not IS_WINDOWS:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("::1", port))
    except OSError as e:
        return e.errno in _ADDR_IN_USE
    finally:
        sock.close()
    return False


def _ask_port(port: int, path: str, timeout: float) -> dict | None:
    import http.client
    conn = http.client.HTTPConnection(HOST, port, timeout=timeout)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        if resp.status != 200:
            return None
        data = json.loads(resp.read())
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        conn.close()
    return data if isinstance(data, dict) else None


def _our_copy_on(port: int) -> dict | None:
    hello = _ask_port(port, "/api/hello", 2)
    if hello is not None and hello.get("app") == APP_ID:
        return hello
    return None


def same_folder(info: dict | None) -> bool:
    folder = str((info or {}).get("folder") or "")
    if not folder:
        return True
    return os.path.normcase(os.path.abspath(folder)) == os.path.normcase(os.path.abspath(str(paths.PROJECT)))


def choose_port(first: int, tries: int) -> tuple[socket.socket | None, int, dict | None]:
    """(сокет, порт, None) — порт свободен и занят нами; (None, порт, сведения) — там уже
    работает эта программа из этой же папки; (None, first, None) — все порты заняты."""
    for port in range(first, min(first + tries, 65536)):
        sock = bind_port(port)
        if sock is not None:
            if not _ipv6_port_taken(port):
                return sock, port, None
            sock.close()
            continue
        ours = _our_copy_on(port)
        if ours is not None and same_folder(ours):
            return None, port, ours
    return None, first, None


def show_running_copy(port: int, info: dict) -> None:
    url = f"http://{HOST}:{port}"
    print(f"  Программа уже работает в другом окне: {url}")
    print("  Открываю её в браузере. Это окно можно закрыть.")
    log.info("порт %d: уже работает копия программы из этой папки (%s)", port, info.get("version"))
    if os.getenv(NO_BROWSER_ENV) != "1":
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass


def open_browser_when_ready(host: str, port: int) -> None:
    """Открывает браузер только когда сервер начал отвечать."""
    import http.client
    import webbrowser
    url = f"http://{host}:{port}"
    for _ in range(60):
        time.sleep(0.5)
        try:
            conn = http.client.HTTPConnection(host, port, timeout=1)
            conn.request("GET", "/api/hello")
            resp = conn.getresponse()
            if resp.status == 200:
                conn.close()
                break
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
