from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import psutil

from .config import PROCESS_STOP_TIMEOUT
from .security import redact_text


class BotProcess:
    def __init__(
        self,
        bot_id: int,
        command: list[str],
        cwd: str,
        env: dict[str, str],
        on_line: Callable[[int, str, str], None],
        on_exit: Callable[[int, int | None], None],
        secret_values: list[str] | None = None,
    ):
        self.bot_id = bot_id
        self.command = command
        self.cwd = cwd
        self.env = env
        self.on_line = on_line
        self.on_exit = on_exit
        self.secret_values = secret_values or []
        self.proc: subprocess.Popen | None = None
        self._threads: list[threading.Thread] = []
        self.started_at: float | None = None

    @property
    def pid(self) -> int | None:
        if self.proc and self.proc.poll() is None:
            return self.proc.pid
        return None

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> int:
        if self.is_running():
            return self.proc.pid
        popen_kwargs = {
            "cwd": self.cwd,
            "env": self.env,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "bufsize": 1,
            "text": True,
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        self.proc = subprocess.Popen(self.command, **popen_kwargs)
        self.started_at = time.time()
        t_out = threading.Thread(target=self._pump, args=("stdout", self.proc.stdout), daemon=True)
        t_err = threading.Thread(target=self._pump, args=("stderr", self.proc.stderr), daemon=True)
        t_wait = threading.Thread(target=self._wait, daemon=True)
        self._threads = [t_out, t_err, t_wait]
        for t in self._threads:
            t.start()
        return self.proc.pid

    def _pump(self, stream: str, pipe) -> None:
        try:
            for raw in iter(pipe.readline, ""):
                line = redact_text(raw.rstrip("\n"), self.secret_values)
                try:
                    self.on_line(self.bot_id, stream, line)
                except Exception:
                    pass
        except Exception:
            pass

    def _wait(self) -> None:
        if not self.proc:
            return
        code = self.proc.wait()
        try:
            self.on_exit(self.bot_id, code)
        except Exception:
            pass

    def stop(self, timeout: int = PROCESS_STOP_TIMEOUT) -> bool:
        if not self.proc or self.proc.poll() is not None:
            return True
        pid = self.proc.pid
        try:
            if sys.platform == "win32":
                self.proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
        except Exception:
            try:
                self.proc.terminate()
            except Exception:
                pass
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                if sys.platform != "win32":
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                else:
                    self.proc.kill()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=3)
            except Exception:
                pass
        return self.proc.poll() is not None

    def send_signal(self, sig: int) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(sig)


class ProcessRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._procs: dict[int, BotProcess] = {}

    def get(self, bot_id: int) -> BotProcess | None:
        with self._lock:
            return self._procs.get(bot_id)

    def register(self, proc: BotProcess) -> None:
        with self._lock:
            self._procs[proc.bot_id] = proc

    def unregister(self, bot_id: int) -> None:
        with self._lock:
            self._procs.pop(bot_id, None)

    def is_running(self, bot_id: int) -> bool:
        proc = self.get(bot_id)
        return bool(proc and proc.is_running())

    def pid(self, bot_id: int) -> int | None:
        proc = self.get(bot_id)
        return proc.pid if proc else None

    def stop(self, bot_id: int) -> bool:
        proc = self.get(bot_id)
        if not proc:
            return True
        ok = proc.stop()
        self.unregister(bot_id)
        return ok

    def running_ids(self) -> list[int]:
        with self._lock:
            return [bid for bid, p in self._procs.items() if p.is_running()]


def process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        return psutil.pid_exists(pid) and psutil.Process(pid).is_running()
    except Exception:
        return False
