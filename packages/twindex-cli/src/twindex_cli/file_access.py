from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path
from threading import Lock

from twindex_core.source_io import AcquiredSource, MAX_BYTES, MAX_ZIP_ENTRIES


def decode_picker_result(payload: str) -> list[AcquiredSource] | None:
    response = json.loads(payload)
    if response.get("status") == "cancelled":
        return None
    if response.get("status") != "ok":
        raise ValueError(
            "Системное окно не смогло прочитать источник: "
            + response.get("message", "неизвестная ошибка")
        )
    items = response["sources"]
    if len(items) > MAX_ZIP_ENTRIES:
        raise ValueError("Слишком много файлов")
    sources, total = [], 0
    for item in items:
        if len(item["raw"]) > ((MAX_BYTES + 2) // 3) * 4:
            raise ValueError("Файл превышает 20 MiB")
        raw = base64.b64decode(item["raw"], validate=True)
        total += len(raw)
        if len(raw) > MAX_BYTES or total > 100 * 1024 * 1024:
            raise ValueError("Превышен лимит импорта")
        sources.append(
            AcquiredSource(
                raw=raw, title=item["title"], uri=item["uri"], suffix=item["suffix"]
            )
        )
    return sources


class NativeFilePicker:
    def __init__(self) -> None:
        self._lock = Lock()
        self._process: subprocess.Popen | None = None
        self._closed = False

    def pick(
        self, target: Path | None = None, *, directory: bool = False
    ) -> list[AcquiredSource] | None:
        if sys.platform != "darwin":
            raise ValueError(
                "Системный выбор доступен на macOS; вставьте путь к источнику"
            )
        with self._lock:
            if self._closed:
                return None
            if self._process is not None:
                raise ValueError("Окно выбора уже открыто")
            process = subprocess.Popen(
                [sys.executable, "-m", "twindex_cli.native_picker"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._process = process
        try:
            output, _ = process.communicate(
                json.dumps(
                    {"target": str(target) if target else None, "directory": directory}
                ),
                timeout=300,
            )
            if process.returncode:
                raise ValueError(
                    "Окно выбора файла закрылось с ошибкой; попробуйте ещё раз"
                )
            return decode_picker_result(output)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise ValueError(
                "Выбор файла отменён по таймауту; откройте окно снова"
            ) from None
        finally:
            with self._lock:
                self._process = None

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._process and self._process.poll() is None:
                self._process.kill()
