"""Browser sign-in on PC for stores whose login page needs a real browser.

Opens Chrome, Edge or Chromium in a separate window with a throw-away profile,
drives it over the DevTools protocol and waits for the sign-in page to redirect to
the store's app callback (for Lidl ``com.lidlplus.app://callback?code=...``). A
normal browser cannot open that link, but DevTools still sees it, so the code is
captured without the user copying anything.

Only the Python standard library is used, including a minimal WebSocket client.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import platform
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path


class BrowserLoginError(RuntimeError):
    pass


def find_browser() -> str | None:
    override = os.getenv("RECEIPTS_BROWSER")
    if override:
        return override
    system = platform.system()
    candidates: list[str] = []
    if system == "Windows":
        roots = [os.getenv(k) for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        for root in filter(None, roots):
            candidates += [
                rf"{root}\Google\Chrome\Application\chrome.exe",
                rf"{root}\Microsoft\Edge\Application\msedge.exe",
                rf"{root}\BraveSoftware\Brave-Browser\Application\brave.exe",
                rf"{root}\Chromium\Application\chrome.exe",
            ]
    elif system == "Darwin":
        for app in ("Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium"):
            candidates.append(f"/Applications/{app}.app/Contents/MacOS/{app}")
            candidates.append(str(Path.home() / f"Applications/{app}.app/Contents/MacOS/{app}"))
    else:
        for name in (
            "google-chrome",
            "google-chrome-stable",
            "chromium",
            "chromium-browser",
            "microsoft-edge",
            "microsoft-edge-stable",
            "brave-browser",
        ):
            found = shutil.which(name)
            if found:
                candidates.append(found)
    return next((c for c in candidates if Path(c).exists()), None)


class _WebSocket:
    """Just enough of RFC 6455 for a local DevTools connection: text frames, no TLS."""

    def __init__(self, url: str, timeout: float = 5.0):
        parts = urllib.parse.urlparse(url)
        self.sock = socket.create_connection((parts.hostname, parts.port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {parts.path} HTTP/1.1\r\nHost: {parts.hostname}:{parts.port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode())
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise BrowserLoginError("the browser closed the DevTools connection")
            response += chunk
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise BrowserLoginError("the browser refused the DevTools connection")
        self.buffer = response.split(b"\r\n\r\n", 1)[1]

    def send(self, text: str) -> None:
        payload = text.encode()
        header = bytearray([0x81])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        self.sock.sendall(bytes(header) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def _read(self, n: int) -> bytes:
        while len(self.buffer) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise BrowserLoginError("the browser window was closed")
            self.buffer += chunk
        data, self.buffer = self.buffer[:n], self.buffer[n:]
        return data

    def recv(self) -> str | None:
        """Return the next text message, or None on timeout."""
        message = b""
        while True:
            try:
                b1, b2 = self._read(2)
            except TimeoutError:
                return None
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b2 & 0x80 else None
            data = self._read(length)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            opcode = b1 & 0x0F
            if opcode == 0x8:
                raise BrowserLoginError("the browser window was closed")
            if opcode in (0x9, 0xA):  # ping/pong
                continue
            message += data
            if b1 & 0x80:
                return message.decode("utf-8", "replace")

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.sock.close()


def _find_prefix(value, prefix: str) -> str | None:
    if isinstance(value, str):
        index = value.find(prefix)
        if index >= 0:
            return value[index:].split()[0].strip("\"'<>")
        return None
    if isinstance(value, dict):
        value = value.values()
    if isinstance(value, (list, tuple, type({}.values()))):
        for item in value:
            found = _find_prefix(item, prefix)
            if found:
                return found
    return None


def capture_callback(
    url: str,
    prefix: str,
    *,
    timeout: float = 600,
    cancelled=lambda: False,
    browser: str | None = None,
    extra_args: list[str] | None = None,
) -> str:
    """Open ``url`` in a browser window and return the first URL starting with ``prefix``."""
    executable = browser or find_browser()
    if not executable:
        raise BrowserLoginError("No Chrome, Edge, Brave or Chromium browser was found on this computer.")
    profile = tempfile.mkdtemp(prefix="receipts-login-")
    args = [
        executable,
        f"--user-data-dir={profile}",
        "--remote-debugging-port=0",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=ExternalProtocolDialog",
        "--window-size=520,860",
        *(extra_args or []),
        "about:blank",
    ]
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    process = subprocess.Popen(
        args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags
    )
    ws = None
    try:
        port_file = Path(profile) / "DevToolsActivePort"
        deadline = time.time() + 20
        while not port_file.exists() or not port_file.read_text().strip():
            if process.poll() is not None or time.time() > deadline:
                raise BrowserLoginError("The browser did not start.")
            time.sleep(0.1)
        port = int(port_file.read_text().split()[0])
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        targets = []
        while time.time() < deadline and not targets:
            try:
                with opener.open(f"http://127.0.0.1:{port}/json/list", timeout=2) as response:
                    targets = [t for t in json.load(response) if t.get("type") == "page"]
            except OSError:
                time.sleep(0.2)
        if not targets:
            raise BrowserLoginError("Could not attach to the browser window.")
        ws = _WebSocket(targets[0]["webSocketDebuggerUrl"], timeout=0.5)
        for n, method in enumerate(("Network.enable", "Page.enable"), 1):
            ws.send(json.dumps({"id": n, "method": method}))
        ws.send(json.dumps({"id": 3, "method": "Page.navigate", "params": {"url": url}}))

        end = time.time() + timeout
        while time.time() < end:
            if cancelled():
                raise BrowserLoginError("Sign-in cancelled.")
            if process.poll() is not None:
                raise BrowserLoginError("The browser window was closed before sign-in finished.")
            text = ws.recv()
            if not text or prefix not in text.replace("\\/", "/"):
                continue
            try:
                found = _find_prefix(json.loads(text), prefix)
            except ValueError:
                found = None
            if found:
                return found
        raise BrowserLoginError("Sign-in timed out.")
    finally:
        if ws:
            ws.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        shutil.rmtree(profile, ignore_errors=True)
