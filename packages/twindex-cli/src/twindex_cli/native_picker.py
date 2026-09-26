"""Short-lived macOS UI process; authorized bytes, not privileged paths, cross IPC."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

from twindex_core.source_io import read_local_sources


def read_authorized_url(url):
    scoped = url.startAccessingSecurityScopedResource()
    try:
        return read_local_sources(Path(str(url.path())))
    finally:
        if scoped:
            url.stopAccessingSecurityScopedResource()


def choose_and_read(request: dict):
    # AppKit must run on this process's main thread, never in a Textual worker.
    from AppKit import (
        NSApplication,
        NSApplicationActivationPolicyAccessory,
        NSModalResponseOK,
        NSOpenPanel,
    )
    from Foundation import NSURL

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    panel = NSOpenPanel.openPanel()
    directory = bool(request.get("directory"))
    panel.setTitle_("Twindex · Добавить источник")
    panel.setPrompt_("Прочитать")
    panel.setMessage_(
        "Выберите папку Markdown. Twindex прочитает её заметки и сохранит локальные снимки. Модели ничего не отправляется."
        if directory
        else "Выберите файл для чтения и локального снимка. Модели ничего не отправляется."
    )
    panel.setCanChooseFiles_(not directory)
    panel.setCanChooseDirectories_(directory)
    panel.setAllowsMultipleSelection_(False)
    panel.setResolvesAliases_(False)
    target = request.get("target")
    if target:
        path = Path(target).expanduser()
        panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(path.parent)))
        panel.setNameFieldStringValue_(path.name)
    app.activateIgnoringOtherApps_(True)
    if panel.runModal() != NSModalResponseOK:
        return {"status": "cancelled"}
    sources = read_authorized_url(panel.URL())
    return {
        "status": "ok",
        "sources": [
            {
                "raw": base64.b64encode(item.raw).decode("ascii"),
                "title": item.title,
                "uri": item.uri,
                "suffix": item.suffix,
            }
            for item in sources
        ],
    }


def main() -> None:
    try:
        request = json.loads(sys.stdin.read(8192))
        result = choose_and_read(request)
    except Exception as exc:
        result = {"status": "error", "message": str(exc), "type": type(exc).__name__}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
