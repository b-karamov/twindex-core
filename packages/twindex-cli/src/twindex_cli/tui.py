from __future__ import annotations

import json
import shlex
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Callable, TypeVar

from rich.markup import escape
from rich.text import Text
from rich.style import Style
from textual.widgets.text_area import TextAreaTheme
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.timer import Timer
from textual.widgets import (
    Button,
    Input,
    Label,
    ListItem,
    ListView,
    Select,
    Static,
    TextArea,
)

from twindex_core.engine import KnowledgeEngine, Provider, VisionUnavailable
from twindex_core.models import (
    Answer,
    Changeset,
    Commit,
    ProposalOperation,
    SourceSnapshot,
)
from twindex_core.provider import OpenAICompatibleProvider
from twindex_core.sources import (
    ConversationFile,
    SourceIngestor,
    conversation_origin,
    conversation_preview,
    default_conversation_roots,
    discover_conversations,
)
from twindex_core.vault import ConflictError, Vault
from twindex_core.source_adapters import SUPPORTED_SUFFIXES
from twindex_core.conversations import conversation_text
from .file_access import NativeFilePicker
from .ui_text import transcript_text
from .diagnostics import write_model_report


T = TypeVar("T")
SUPPORTED_SOURCE_FORMATS = (
    "TXT, Markdown, JSON, JSONL, PDF, DOCX, PNG/JPEG и Obsidian ZIP"
)
SUPPORTED_SOURCE_SUFFIXES = SUPPORTED_SUFFIXES


class TwindexApp(App):
    """Chat-first interface over the local Twindex vault."""

    TITLE = "Twindex"
    CSS = """
    Screen {
        background: ansi_default;
        color: ansi_default;
    }

    #header-frame, #input-frame {
        height: auto;
        align-horizontal: center;
    }

    #topbar {
        width: 100%;
        max-width: 100%;
        height: auto;
        min-height: 3;
        padding: 1 3 0 3;
        background: ansi_default;
        align: left middle;
        border-bottom: solid #666666;
    }
    #brand {
        height: 1;
        width: 12;
        color: #e5b567;
        text-style: bold;
        content-align: left middle;
    }
    #environment {
        height: auto;
        width: 1fr;
        color: #aaaaaa;
        content-align: left middle;
    }
    #vault-label {
        height: 1;
        width: auto;
        color: #aaaaaa;
        content-align: right middle;
    }

    #timeline {
        height: 1fr;
        padding: 1 2 0 3;
        align-horizontal: center;
        scrollbar-size: 1 1;
        scrollbar-gutter: stable;
        overflow-x: hidden;
        scrollbar-color: #808080;
        scrollbar-background: ansi_default;
    }
    #flow {
        height: auto;
        width: 100%;
        max-width: 100%;
    }
    #welcome-art {
        height: 4;
        color: #e5b567;
        margin-bottom: 1;
    }
    #conversation {
        height: auto;
        color: ansi_default;
    }
    #welcome-intro {
        height: auto;
    }
    #welcome-marker {
        width: 3;
        height: 1;
        color: #e5b567;
    }
    #welcome-copy {
        width: 1fr;
        height: auto;
        color: ansi_default;
    }

    #context-panel {
        height: auto;
        margin: 1 0;
        padding: 0 0 0 2;
        border-left: solid #808080;
        background: ansi_default;
    }
    #context-title {
        height: auto;
        color: #e5b567;
        text-style: bold;
        margin-bottom: 1;
    }
    #context-body {
        height: auto;
        color: ansi_default;
    }
    #chooser-preview {
        height: auto;
        max-height: 6;
        overflow-y: auto;
        margin-bottom: 1;
        padding: 0 1;
        color: ansi_default;
        background: ansi_default;
        border-left: solid #808080;
    }
    #chooser {
        height: auto;
        max-height: 13;
        margin-top: 1;
        background: ansi_default;
    }
    #chooser ListItem {
        height: 2;
        padding: 0 1;
        color: ansi_default;
        border-left: solid transparent;
    }
    #chooser ListItem.-highlight {
        background: ansi_default;
        color: #e5b567;
        text-style: bold;
        border-left: solid #e5b567;
    }
    #operations {
        height: auto;
        max-height: 15;
        margin-top: 1;
        background: ansi_default;
    }
    #operations ListItem {
        height: 2;
        padding: 0 1;
        border-left: solid transparent;
    }
    #operations ListItem.-highlight {
        background: ansi_default;
        color: #e5b567;
        text-style: bold;
        border-left: solid #e5b567;
    }
    #chooser, #operations, #chooser-preview {
        scrollbar-size: 1 1;
        scrollbar-color: #808080;
        scrollbar-color-hover: #aaaaaa;
        scrollbar-color-active: #e5b567;
        scrollbar-background: ansi_default;
        scrollbar-background-hover: ansi_default;
        scrollbar-background-active: ansi_default;
        overflow-x: hidden;
    }
    #editor {
        height: 11;
        margin-top: 1;
        border: solid #e5b567;
        background: ansi_default;
        color: ansi_default;
    }
    .hidden {
        display: none;
    }

    #composer-shell {
        height: auto;
        width: 100%;
        max-width: 100%;
        padding: 0 3 1 3;
        margin-top: 1;
        background: ansi_default;
    }
    #status {
        height: auto;
        color: #aaaaaa;
        padding: 0 0 1 1;
    }
    #composer, #section-search {
        height: 3;
        border: round #808080;
        background: ansi_default;
        color: ansi_default;
        padding: 0 1;
    }
    #composer:focus, #section-search:focus {
        border: round #e5b567;
    }
    #composer > .input--placeholder {
        color: #aaaaaa;
    }
    #hints {
        height: auto;
        color: #aaaaaa;
        padding-left: 1;
    }
    #next-actions {
        height: auto;
        margin-top: 1;
    }
    #next-actions Button {
        width: auto;
        min-width: 12;
        height: 3;
        padding: 0 2;
        margin-right: 2;
        border: round #808080;
        background: ansi_default;
        color: ansi_default;
        text-style: none;
    }
    #next-actions #next-primary {
        color: #e5b567;
        border: round #e5b567;
    }
    #next-actions Button:focus, #next-actions Button:hover {
        text-style: bold;
        border: round #e5b567;
        background: ansi_default;
    }
    #next-actions Button:disabled, #section-tools Button:disabled {
        opacity: 100%; text-opacity: 100%; color: #808080; background: ansi_default;
        border: round #666666; tint: transparent;
    }

    Screen.narrow #topbar {
        padding: 0 1;
    }
    Screen.narrow #vault-label {
        display: none;
    }
    Screen.narrow #timeline {
        padding: 1 0 0 1;
    }
    Screen.narrow #context-panel {
        padding: 0 0 0 1;
    }
    Screen.compact #next-actions {
        layout: vertical;
    }
    Screen.narrow #chooser-preview {
        max-height: 5;
    }
    Screen.narrow #operations {
        max-height: 5;
    }
    Screen.narrow #welcome-art {
        height: auto;
    }
    Screen.narrow #composer-shell {
        padding: 0 1 1 1;
    }
    #next-actions { margin: 1 0 0 3; }
    Screen.narrow #context-panel { padding-left: 2; }
    #navigation { height: 3; padding: 0 3; layout: grid; grid-size: 5; }
    #navigation Button, #section-tools Button {
        min-width: 0; height: 3; padding: 0 1; border: round #808080;
        background: ansi_default; color: ansi_default;
    }
    #navigation Button.active-nav, #navigation Button:focus,
    #section-tools Button:focus { border: round #e5b567; color: #e5b567; }
    #section-tools { height: auto; }
    #section-tools Button { width: auto; margin-right: 1; }
    #section-search { width: 1fr; min-width: 10; height: 3; background: ansi_default; }
    Screen.narrow #navigation { padding: 0 1; grid-size: 3; height: 6; }
    Screen.compact #section-tools { layout: vertical; }
    Screen.compact #section-search { width: 100%; }
    #settings-form { height: auto; }
    #settings-form, #edit-fields { height: auto; margin-top: 1; }
    #settings-form Label, #edit-fields Label { margin-top: 0; color: #aaaaaa; }
    #settings-form Input, #edit-fields Input { margin: 0 0 1 0; }
    #settings-form Input, #settings-form Select, #edit-fields Input {
        height: 3; border: round #808080; padding: 0 1;
        background: ansi_default; color: ansi_default;
    }
    #settings-form Input:focus, #edit-fields Input:focus {
        border: round #e5b567;
    }
    #settings-form Select { border: none; padding: 0; }
    Select > SelectCurrent {
        border: round #808080; background: ansi_default; color: ansi_default;
    }
    Select:focus > SelectCurrent { border: round #e5b567; }
    SelectOverlay { background: ansi_default; color: ansi_default; border: round #e5b567; }
    #settings-form Button {
        width: auto; height: 3; min-width: 12; margin-top: 1;
        padding: 0 2; border: round #808080; background: ansi_default;
        color: ansi_default; text-style: none;
    }
    #settings-form Button:focus { border: round #e5b567; color: #e5b567; }
    #editor {
        height: 11; border: round #808080; margin-top: 0;
        scrollbar-color: #808080; scrollbar-color-hover: #aaaaaa;
        scrollbar-color-active: #e5b567; scrollbar-background: ansi_default;
        scrollbar-background-hover: ansi_default; scrollbar-background-active: ansi_default;
    }
    #editor:focus { border: round #e5b567; }
    """

    BINDINGS = [
        Binding("ctrl+p", "propose", "Propose", show=False, priority=True),
        Binding("ctrl+e", "edit", "Edit", show=False, priority=True),
        Binding("ctrl+s", "stage", "Stage", show=False, priority=True),
        Binding("ctrl+g", "garden", "Garden", show=False, priority=True),
        Binding("ctrl+l", "history", "History", show=False, priority=True),
        Binding("ctrl+q", "quit", "Quit", show=False, priority=True),
        Binding("ctrl+c,super+c", "copy_text", "Copy", show=False, priority=True),
        Binding("space", "toggle_operation", "Select", show=False),
        Binding("escape", "navigation_back", "Back", show=False, priority=True),
        Binding("slash", "command", "Command", show=False),
    ]

    def __init__(
        self,
        vault_path: Path,
        *,
        provider_factory: Callable[[], Provider] | None = None,
        args=None,
    ):
        super().__init__(ansi_color=True)
        self.vault = Vault.open(vault_path)
        self.provider_factory = provider_factory
        self.args = args
        self.source_id: str | None = None
        self.changeset_id: str | None = None
        self.operation_id: str | None = None
        self.commit_id: str | None = None
        self._model_busy = False
        self._commit_armed = False
        self._revert_armed: str | None = None
        self._messages: list[str] = []
        self.profile = getattr(args, "profile", "ollama")
        self.model_name = getattr(args, "model", None)
        self.embedding_model = getattr(args, "embedding_model", None)
        self._source_choices: list[SourceSnapshot] = []
        self._commit_choices: list[Commit] = []
        self._conversation_choices: list[ConversationFile] = []
        self._conversation_previews: dict[Path, str] = {}
        self._chooser_mode: str | None = None
        self._copy_context_text = ""
        self._busy_timer: Timer | None = None
        self._busy_started_at = 0.0
        self._busy_frame = 0
        self._busy_label = ""
        self._next_commands: dict[str, str] = {}
        self._section = "chat"
        self._view_stack: list[dict] = []
        self._restoring_view = False
        self._card_choices: list[str] = []
        self._show_archived = False
        self._delete_confirmation: tuple[str, list[str]] | None = None
        self._editor_original = ""
        self._editor_mode = "raw"
        self._edit_base: dict = {}
        self._edit_initial: dict = {}
        self._last_question = ""
        self._last_diagnostic_path: Path | None = None
        self._dirty_destination: str | None = None
        self._nav_epoch = 0
        self._model_origin_epoch = 0
        self._request_source_id: str | None = None
        self._file_picker = NativeFilePicker()
        self._native_busy = False
        self._native_origin_epoch = 0
        self._card_links: list[str] = []

    def compose(self) -> ComposeResult:
        with Horizontal(id="header-frame"):
            with Horizontal(id="topbar"):
                yield Static("Twindex", id="brand", markup=False)
                yield Static(self._environment_label(), id="environment", markup=False)
                yield Static(self._vault_label(), id="vault-label", markup=False)
        with Horizontal(id="navigation"):
            for key, label in (
                ("chat", "Разговор"),
                ("sources", "Источники"),
                ("cards", "Карточки"),
                ("history", "История"),
                ("settings", "Настройки"),
            ):
                yield Button(label, id=f"nav-{key}")
        with VerticalScroll(id="timeline"):
            with Vertical(id="flow"):
                yield Static(
                    "╭────╮\n│ ╭──┴─╮   [bold]T W I N D E X[/]\n"
                    "╰─┤ ◇  │   [default]Ваши знания, связанные воедино.[/]\n  ╰────╯",
                    id="welcome-art",
                )
                with Horizontal(id="welcome-intro"):
                    yield Static("◈", id="welcome-marker")
                    yield Static("", id="welcome-copy", markup=True)
                yield Static("", id="conversation", markup=True)
                with Vertical(id="context-panel", classes="hidden"):
                    yield Static("", id="context-title", markup=True)
                    yield Static("", id="context-body", markup=True)
                    yield ListView(id="chooser", classes="hidden")
                    yield ListView(id="operations", classes="hidden")
                    yield Static(
                        "", id="chooser-preview", classes="hidden", markup=True
                    )
                    with Vertical(id="edit-fields", classes="hidden"):
                        yield Label("Название", id="edit-title-label")
                        yield Input(id="edit-title")
                        yield Label("Тип")
                        yield Input(id="edit-type")
                        yield Label(
                            "Куда ведёт связь · ID карточки",
                            id="edit-target-label",
                            classes="hidden",
                        )
                        yield Input(id="edit-target", classes="hidden")
                        yield Label("Текст", id="edit-content-label")
                    yield TextArea(
                        "", id="editor", classes="hidden", tab_behavior="focus"
                    )
                    with Vertical(id="settings-form", classes="hidden"):
                        yield Label("Профиль")
                        yield Select(
                            [
                                ("Ollama · локально", "ollama"),
                                ("Облако", "cloud"),
                                ("Свой endpoint · параметры CLI", "custom"),
                            ],
                            value=self.profile,
                            allow_blank=False,
                            id="settings-profile",
                        )
                        yield Label("Генерация")
                        yield Input(id="settings-model")
                        yield Label("Embeddings")
                        yield Input(id="settings-embedding")
                        yield Label("Каталог vault · данные не переносятся")
                        yield Input(id="settings-vault-path")
                        yield Button("Открыть этот vault", id="settings-vault")
                        yield Button("Доступ к диалогам", id="settings-permissions")
                with Horizontal(id="next-actions", classes="hidden"):
                    yield Button("", id="next-primary")
                    yield Button("", id="next-secondary")
        with Horizontal(id="input-frame"):
            with Vertical(id="composer-shell"):
                yield Static("", id="status", classes="hidden", markup=False)
                with Horizontal(id="section-tools", classes="hidden"):
                    yield Button("Назад · Esc", id="back")
                    yield Button("Новый разговор", id="new-chat")
                    yield Input(placeholder="Поиск в списке…", id="section-search")
                    yield Button(
                        "Удалить / архив", id="source-remove", classes="hidden"
                    )
                    yield Button("Архив", id="source-archive", classes="hidden")
                    yield Button(
                        "Проверить и сохранить", id="review-save", classes="hidden"
                    )
                    yield Button("Сохранить правки", id="form-save", classes="hidden")
                yield Input(
                    placeholder="Вопрос по знаниям или путь к файлу…", id="composer"
                )
                yield Static(
                    "Enter отправить · Tab выбрать действие · /help · /model",
                    id="hints",
                )

    def on_mount(self) -> None:
        editor = self.query_one("#editor", TextArea)
        editor.register_theme(
            TextAreaTheme(
                "twindex",
                cursor_style=Style(reverse=True),
                cursor_line_style=Style(),
                selection_style=Style(color="black", bgcolor="#e5b567"),
            )
        )
        editor.theme = "twindex"
        self._show_welcome()
        self._update_navigation()
        self.query_one("#composer", Input).focus()
        self.call_after_refresh(self._fit_timeline)

    def on_resize(self, event) -> None:
        self.screen.set_class(event.size.width < 72, "narrow")
        self.screen.set_class(event.size.width < 48, "compact")
        self._refresh_header()
        self.call_after_refresh(self._fit_timeline)
        self.call_after_refresh(self._render_messages, False)

    def _fit_timeline(self) -> None:
        self.query_one("#timeline").styles.max_height = None

    def on_unmount(self) -> None:
        self._file_picker.close()
        if self._busy_timer is not None:
            self._busy_timer.stop()
        self.vault.close()

    def _environment_label(self) -> str:
        location = {
            "ollama": "Локально",
            "cloud": "Облако",
            "custom": "Custom",
        }.get(self.profile, self.profile)
        if self.is_running and self.screen.has_class("narrow"):
            return f"{location} · {self._active_model_name()}"
        return f"{location} · {self._active_model_name()} · embed {self._active_embedding_name()}"

    def _active_model_name(self) -> str:
        return self.model_name or (
            "gpt-5.6-luna" if self.profile == "cloud" else "qwen3-vl:4b-instruct"
        )

    def _active_embedding_name(self) -> str:
        return self.embedding_model or (
            "text-embedding-3-small"
            if self.profile == "cloud"
            else "qwen3-embedding:0.6b"
        )

    def _vault_label(self) -> str:
        return f"vault: {self.vault.root.name}"

    def _refresh_header(self) -> None:
        self.query_one("#environment", Static).update(self._environment_label())
        self.query_one("#vault-label", Static).update(self._vault_label())

    def _show_welcome(self) -> None:
        sources = self.vault.list_sources()
        cards = self.vault.list_cards()
        if sources or cards:
            summary = (
                f"[bold]С возвращением.[/]\n\n"
                f"Карточки: {len(cards)} · Источники: {len(sources)}\n"
                "Новый разговор. Источник не выбран.\n"
                "Задайте вопрос по знаниям или откройте источники."
            )
        else:
            summary = (
                "[bold]С чего начнём?[/]\n\n"
                "Добавьте источник: заметку, PDF, ссылку или диалог Claude/Codex.\n"
                "Я предложу карточки и связи. Вы решите, что сохранить."
            )
        self.query_one("#welcome-copy", Static).update(summary)
        self.query_one("#welcome-intro").remove_class("hidden")
        self._render_messages()
        self._set_next_actions(
            ("Добавить источник", "/add"),
            ("Открыть источники", "/inbox")
            if sources
            else ("Найти диалог", "/discover"),
        )

    def _set_next_actions(
        self, primary: tuple[str, str], secondary: tuple[str, str]
    ) -> None:
        self._next_commands = {}
        for widget_id, (label, command) in zip(
            ("next-primary", "next-secondary"), (primary, secondary)
        ):
            button = self.query_one(f"#{widget_id}", Button)
            button.remove_class("-active")
            button.label = label
            button.disabled = False
            self._next_commands[widget_id] = command
        self.query_one("#next-actions").remove_class("hidden")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        widget_id = event.button.id or ""
        if widget_id.startswith("nav-"):
            # A cancelled transition must be immediately retryable after Escape.
            event.button.remove_class("-active")
            self._navigate_section(widget_id.removeprefix("nav-"))
            return
        if widget_id == "back":
            self.action_navigation_back()
            return
        if widget_id == "source-remove":
            self._remove_source_prompt()
            return
        if widget_id == "source-archive":
            self._show_archived = not self._show_archived
            self._render_inbox()
            return
        if widget_id == "new-chat":
            self._clear_session()
            return
        if widget_id == "review-save":
            self.action_commit()
            return
        if widget_id == "form-save":
            try:
                if not self.query_one("#editor").has_class("hidden"):
                    self.action_save_edit()
                else:
                    self._save_settings()
            except Exception as exc:
                self._present_error(exc)
            return
        if widget_id == "settings-vault":
            try:
                path = self.query_one("#settings-vault-path", Input).value.strip()
                if not path:
                    raise ValueError("Укажите каталог vault")
                self._open_vault(Path(path).expanduser())
            except Exception as exc:
                self._present_error(exc)
            return
        if widget_id == "settings-permissions":
            from .cli import _consent

            allowed = _consent(self.vault)
            self._show_context(
                "ДОСТУП К ДИАЛОГАМ",
                "Просмотр диалогов разрешён."
                if allowed
                else "Просмотр диалогов запрещён.\nРазрешение запрашивается до чтения списка и первых фраз.",
            )
            self._set_next_actions(
                ("Отозвать доступ", "/discover revoke")
                if allowed
                else ("Настроить доступ", "/discover"),
                ("Назад", "/back"),
            )
            return
        command = self._next_commands.get(event.button.id or "")
        if command is None:
            return
        if command == "/add":
            self._section = "sources"
            self._show_context(
                "Добавить источник",
                "Перетащите файл в терминал или вставьте путь / URL в строку ниже.\n"
                f"Поддерживаются: {SUPPORTED_SOURCE_FORMATS}.",
            )
            if sys.platform == "darwin":
                self._set_next_actions(
                    ("Выбрать файл…", "/pick"), ("Выбрать папку…", "/pick folder")
                )
            composer = self.query_one("#composer", Input)
            composer.remove_class("hidden")
            composer.value = "/add "
            composer.cursor_position = len(composer.value)
            composer.focus()
            return
        try:
            self._dispatch(command)
        except Exception as exc:
            self._present_error(exc)

    def _append_user(self, text: str) -> None:
        self.query_one("#welcome-art").add_class("hidden")
        self.query_one("#welcome-intro").add_class("hidden")
        self._messages.append(f"[bold default]›  {escape(text)}[/]")
        self._render_messages()

    def _append_assistant(self, text: str, *, warning: bool = False) -> None:
        color = "#f0a06f" if warning else "default"
        self._messages.append(f"[bold #e5b567]◈[/]  [{color}]{text}[/{color}]")
        self._render_messages()

    def _render_messages(self, scroll: bool = True) -> None:
        conversation = self.query_one("#conversation", Static)
        width = self.query_one("#flow").content_size.width or max(
            10, self.size.width - 7
        )
        conversation.update(transcript_text(self._messages, self.console, width))
        conversation.set_class(not self._messages, "hidden")
        self._update_new_chat_visibility()
        if scroll:
            self.call_after_refresh(
                self.query_one("#timeline", VerticalScroll).scroll_end
            )

    def _status(self, message: str, *, error: bool = False) -> None:
        widget = self.query_one("#status", Static)
        widget.update(message)
        widget.set_class(not bool(message), "hidden")
        widget.styles.color = "#e87f7f" if error else "#e5b567"
        self.call_after_refresh(self._fit_timeline)

    def _begin_model_activity(self, label: str) -> None:
        if self._native_busy:
            raise ValueError("Сначала завершите выбор файла в системном окне")
        self._model_busy = True
        self._busy_label = label
        self._busy_started_at = monotonic()
        self._busy_frame = 0
        self._model_origin_epoch = self._nav_epoch
        self._request_source_id = self.source_id
        if self._busy_timer is None:
            self._busy_timer = self.set_interval(0.12, self._tick_model_activity)
        else:
            self._busy_timer.resume()
        self._tick_model_activity()

    def _tick_model_activity(self) -> None:
        if not self._model_busy:
            return
        frames = ("[=  ]", "[ = ]", "[  =]", "[ = ]")
        frame = frames[self._busy_frame % len(frames)]
        self._busy_frame += 1
        elapsed = int(monotonic() - self._busy_started_at)
        self._status(
            f"{frame} {self._busy_label} · {self._active_model_name()} · {elapsed} с"
        )

    def _end_model_activity(self) -> None:
        self._model_busy = False
        if self._busy_timer is not None:
            self._busy_timer.pause()

    def _show_context(
        self,
        title: str,
        body: str,
        *,
        operations: bool = False,
        editor: bool = False,
        chooser: bool = False,
        preview: bool = False,
        previous_view: dict | None = None,
    ) -> None:
        if not self._model_busy and not self._native_busy:
            self._status("")
        old_title = str(self.query_one("#context-title", Static).render())
        if not self._restoring_view and (
            self.query_one("#context-panel").has_class("hidden")
            or old_title.split(" · ")[0] != title.split(" · ")[0]
        ):
            self._view_stack.append(
                previous_view if previous_view is not None else self._capture_view()
            )
            self._nav_epoch += 1
        if title in {"ИСТОЧНИКИ", "ИСТОЧНИК", "АРХИВ ИСТОЧНИКОВ"}:
            self._section = "sources"
        elif title in {"КАРТОЧКИ", "ПОИСК · ПОДТВЕРЖДЁННЫЕ КАРТОЧКИ"}:
            self._section = "cards"
        elif title == "ИСТОРИЯ":
            self._section = "history"
        elif title.startswith("МОДЕЛЬ"):
            self._section = "settings"
        self.query_one("#next-actions").add_class("hidden")
        self.query_one("#settings-form").add_class("hidden")
        self.query_one("#context-panel").remove_class("hidden")
        self.query_one("#context-title", Static).update(title)
        self.query_one("#context-body", Static).update(body)
        self.query_one("#chooser").set_class(not chooser, "hidden")
        self.query_one("#chooser-preview").set_class(not preview, "hidden")
        self.query_one("#operations").set_class(not operations, "hidden")
        self.query_one("#editor").set_class(not editor, "hidden")
        self.query_one("#edit-fields").set_class(
            not editor or self._editor_mode == "raw", "hidden"
        )
        self._copy_context_text = self._plain_text(f"{title}\n\n{body}")
        self._update_navigation()
        self.call_after_refresh(
            self.query_one("#timeline", VerticalScroll).scroll_home,
            animate=False,
            immediate=True,
        )

    def _capture_view(self) -> dict:
        widgets = (
            "welcome-art",
            "welcome-intro",
            "conversation",
            "context-panel",
            "chooser",
            "chooser-preview",
            "operations",
            "editor",
            "next-actions",
            "settings-form",
            "edit-fields",
        )
        lists = {}
        for key in ("chooser", "operations"):
            view = self.query_one(f"#{key}", ListView)
            lists[key] = (
                [
                    (str(item.query_one(Label).render()), item.name)
                    for item in view.children
                    if isinstance(item, ListItem) and item.query(Label)
                ],
                view.index,
            )
        return {
            "section": self._section,
            "title": str(self.query_one("#context-title", Static).render()),
            "body": str(self.query_one("#context-body", Static).render()),
            "preview": str(self.query_one("#chooser-preview", Static).render()),
            "hidden": {
                key: self.query_one(f"#{key}").has_class("hidden") for key in widgets
            },
            "lists": lists,
            "mode": self._chooser_mode,
            "actions": self._next_commands.copy(),
            "buttons": [
                (
                    str(self.query_one(f"#{key}", Button).label),
                    self.query_one(f"#{key}", Button).disabled,
                )
                for key in ("next-primary", "next-secondary")
            ],
            "ids": (
                self.source_id,
                self.changeset_id,
                self.operation_id,
                self.commit_id,
            ),
            "sources": self._source_choices[:],
            "cards": self._card_choices[:],
            "commits": self._commit_choices[:],
            "copy": self._copy_context_text,
            "links": self._card_links[:],
            "search": self.query_one("#section-search", Input).value,
            "scroll": self.query_one("#timeline").scroll_y,
            "editor": self.query_one("#editor", TextArea).text,
            "edit_values": {
                key: self.query_one(f"#edit-{key}", Input).value
                for key in ("title", "type", "target")
            },
        }

    def _restore_view(self, frame: dict) -> None:
        self._restoring_view = True
        try:
            self._section = frame["section"]
            self.source_id, self.changeset_id, self.operation_id, self.commit_id = (
                frame["ids"]
            )
            self._source_choices = frame["sources"]
            self._card_choices = frame["cards"]
            self._commit_choices = frame["commits"]
            self._card_links = frame["links"]
            self._chooser_mode = frame["mode"]
            for key in ("title", "body"):
                self.query_one(f"#context-{key}", Static).update(frame[key])
            self.query_one("#chooser-preview", Static).update(frame["preview"])
            for key, hidden in frame["hidden"].items():
                self.query_one(f"#{key}").set_class(hidden, "hidden")
            for key, (rows, index) in frame["lists"].items():
                view = self.query_one(f"#{key}", ListView)
                view.clear()
                view.extend(
                    ListItem(Label(label, markup=False), name=name)
                    for label, name in rows
                )
                self.call_after_refresh(self._focus_list, key, index)
            self._next_commands = frame["actions"]
            for key, (label, disabled) in zip(
                ("next-primary", "next-secondary"), frame["buttons"]
            ):
                button = self.query_one(f"#{key}", Button)
                button.label, button.disabled = label, disabled
                button.remove_class("-active")
            self._copy_context_text = frame["copy"]
            self.query_one("#section-search", Input).value = frame["search"]
            self.query_one("#editor", TextArea).text = frame["editor"]
            for key, value in frame["edit_values"].items():
                self.query_one(f"#edit-{key}", Input).value = value
            self._commit_armed = False
            self._update_navigation()
            if not frame["hidden"]["operations"] and self.changeset_id:
                self._render_changeset(self.vault.get_changeset(self.changeset_id))
            elif frame["title"] == "ПРЕДЛОЖЕННАЯ КАРТОЧКА" and self.changeset_id:
                change = self.vault.get_changeset(self.changeset_id)
                self._show_operation(
                    change,
                    next(op for op in change.operations if op.id == self.operation_id),
                )
            self.call_after_refresh(
                self.query_one("#timeline").scroll_to, y=frame["scroll"], animate=False
            )
            if not frame["hidden"]["editor"]:
                self.call_after_refresh(self.query_one("#editor").focus)
            elif frame["hidden"]["chooser"] and frame["hidden"]["operations"]:
                self.call_after_refresh(
                    self.query_one(
                        "#composer" if self._section == "chat" else "#back"
                    ).focus
                )
        finally:
            self._restoring_view = False

    def _focus_list(self, key: str, index: int | None = 0) -> None:
        view = self.query_one(f"#{key}", ListView)
        view.index = index if view.children else None
        if not view.has_class("hidden"):
            view.focus()

    def _focus_control(self, selector: str) -> None:
        widget = self.query_one(selector)
        widget.focus(scroll_visible=False)
        widget.scroll_visible(animate=False, force=True, immediate=True)

    def _update_new_chat_visibility(self) -> None:
        context = not self.query_one("#context-panel").has_class("hidden")
        self.query_one("#new-chat").set_class(
            self._section != "chat" or context or not self._messages, "hidden"
        )

    def _update_navigation(self) -> None:
        context = not self.query_one("#context-panel").has_class("hidden")
        for button in self.query("#navigation Button"):
            button.set_class(button.id == f"nav-{self._section}", "active-nav")
        self.query_one("#composer").set_class(
            self._section != "chat" or context, "hidden"
        )
        self.query_one("#section-tools").remove_class("hidden")
        self.query_one("#back").set_class(
            self._section == "chat" and not context, "hidden"
        )
        self._update_new_chat_visibility()
        listing = self._chooser_mode in {"sources", "cards"} and not self.query_one(
            "#chooser"
        ).has_class("hidden")
        self.query_one("#section-search").set_class(not listing, "hidden")
        title = str(self.query_one("#context-title", Static).render())
        form_save = self.query_one("#form-save", Button)
        form_save.set_class(
            title not in {"РЕДАКТИРОВАНИЕ ПРЕДЛОЖЕНИЯ", "МОДЕЛЬ И ХРАНИЛИЩЕ"}
            or not context,
            "hidden",
        )
        form_save.label = (
            "Сохранить правки"
            if title == "РЕДАКТИРОВАНИЕ ПРЕДЛОЖЕНИЯ"
            else "Сохранить настройки"
        )
        reviewing = context and (title.startswith(("ПРЕДЛОЖЕНИЯ", "ПРЕДЛОЖЕННАЯ")))
        review_button = self.query_one("#review-save", Button)
        review_button.set_class(not reviewing, "hidden")
        if reviewing and self.changeset_id:
            selected_count = sum(
                op.staged
                for op in self.vault.get_changeset(self.changeset_id).operations
            )
            review_button.label = f"Проверить и сохранить ({selected_count})"
            review_button.disabled = selected_count == 0
        self.query_one("#source-remove").set_class(
            title != "ИСТОЧНИК" or not context, "hidden"
        )
        self.query_one("#source-archive").set_class(
            title not in {"ИСТОЧНИКИ", "АРХИВ ИСТОЧНИКОВ"} or not context, "hidden"
        )
        self.query_one("#source-archive", Button).label = (
            "Активные" if self._show_archived else "Архив"
        )
        self.query_one("#hints", Static).update(
            "↑↓ выбрать · Enter открыть · Tab к действиям · Esc назад · Ctrl+Q выйти"
            if listing
            else "Tab действия · Esc назад · / команда · Ctrl+C копировать · Ctrl+Q выйти"
            if context
            else "Enter отправить · Tab действия · /help · Ctrl+Q выйти"
        )
        if context:
            self.query_one("#welcome-art").add_class("hidden")
            self.query_one("#welcome-intro").add_class("hidden")
        self.query_one("#conversation").set_class(
            self._section != "chat" or not self._messages, "hidden"
        )

    def action_command(self) -> None:
        composer = self.query_one("#composer", Input)
        composer.remove_class("hidden")
        composer.value = "/"
        composer.focus()
        composer.cursor_position = 1

    def _navigate_section(self, section: str) -> None:
        if self._guard_editor(section):
            return
        self._nav_epoch += 1
        self._view_stack.clear()
        self._section = section
        self._commit_armed = False
        self._delete_confirmation = None
        self._revert_armed = None
        self._restoring_view = True
        try:
            self._hide_context()
            self.query_one("#section-search", Input).value = ""
            if section == "chat":
                self.source_id = self.changeset_id = self.operation_id = None
                self._show_welcome()
                self.query_one("#welcome-intro").set_class(
                    bool(self._messages), "hidden"
                )
                self.query_one("#composer").focus()
            elif section == "sources":
                self._render_inbox()
            elif section == "cards":
                self._render_cards()
            elif section == "history":
                self._render_history()
            else:
                self._render_model()
        finally:
            self._restoring_view = False
        if not self._model_busy:
            self._status("")
        self._update_navigation()

    def _guard_editor(self, destination: str) -> bool:
        editor = self.query_one("#editor", TextArea)
        if (
            not editor.has_class("hidden")
            and self._editor_snapshot() != self._editor_original
        ):
            self._dirty_destination = destination
            self._show_context(
                "НЕСОХРАНЁННЫЕ ПРАВКИ",
                "Сохранить правки в предложении? Карточки базы не изменятся.\nEscape: продолжить редактирование.",
            )
            self._set_next_actions(
                ("Сохранить и выйти", "/edit-leave save"),
                ("Отбросить правки", "/edit-leave discard"),
            )
            self.query_one("#next-primary").focus()
            return True
        return False

    def action_navigation_back(self) -> None:
        for select in self.query(Select):
            if select.expanded:
                select.expanded = False
                select.focus()
                return
        if self._guard_editor("back"):
            return
        self._nav_epoch += 1
        self._delete_confirmation = None
        self._commit_armed = False
        self._revert_armed = None
        if self._view_stack:
            self._restore_view(self._view_stack.pop())
        else:
            self._navigate_section("chat")

    def _hide_context(self) -> None:
        self.query_one("#context-panel").add_class("hidden")
        self.query_one("#next-actions").add_class("hidden")
        self._chooser_mode = None

    @staticmethod
    def _plain_text(markup: str) -> str:
        try:
            return Text.from_markup(markup).plain
        except Exception:
            return markup

    @staticmethod
    def _locator(value: dict) -> str:
        if not value:
            return "фрагмент"
        if "page" in value:
            return f"Страница {value['page']}"
        if value.get("kind") == "docx":
            if "table" in value:
                return f"Таблица {value['table']} · строка {value['row']} · ячейка {value['cell']} · абзац {value['paragraph']}"
            return f"Абзац {value.get('paragraph', '?')} · DOCX"
        if isinstance(value.get("message_index"), int):
            role = {"user": "пользователь", "assistant": "ассистент"}.get(
                str(value.get("role", "")), "сообщение"
            )
            return f"Сообщение {int(value['message_index']) + 1} · {role}"
        if "line" in value:
            return f"Строка {value['line']}"
        if "start" in value and "end" in value:
            return f"Фрагмент текста · символы {value['start']}–{value['end']}"
        return ", ".join(f"{key}: {item}" for key, item in value.items())

    @staticmethod
    def _short(value: str, length: int = 12) -> str:
        return value if len(value) <= length else value[:length]

    def _refresh_selection(self) -> None:
        sources = self.vault.list_sources()
        changesets = self.vault.list_changesets()
        commits = self.vault.list_commits()
        if self.source_id not in {item.id for item in sources}:
            self.source_id = None
        if not sources:
            self.source_id = None
        if changesets and self.changeset_id not in {item.id for item in changesets}:
            self.changeset_id = changesets[0].id
        if not changesets:
            self.changeset_id = None
        if commits and self.commit_id not in {item.id for item in commits}:
            self.commit_id = commits[0].id
        if not commits:
            self.commit_id = None

    def _source_label(self, source: SourceSnapshot) -> str:
        if source.kind != "conversation":
            return source.title
        origin = (
            "Codex"
            if "codex" in (source.uri or "").lower()
            or source.title.startswith("rollout-")
            else "Claude"
            if "claude" in (source.uri or "").lower()
            else "Диалог"
        )
        for evidence in self.vault.list_evidence(source.id):
            text = conversation_text(
                str(evidence.locator.get("role", "")), evidence.text
            )
            if evidence.locator.get("role") == "user" and text:
                return f"{origin} · {text.splitlines()[0][:80]}"
        return f"{origin} · {source.title[:60]}"

    def _source_status(self, source_id: str) -> str:
        changes = [
            item
            for item in self.vault.list_changesets()
            if source_id in item.source_ids
        ]
        if not changes:
            return "Не анализировался"
        if any(item.status == "committed" for item in changes):
            if any(item.status == "open" for item in changes) or any(
                any(not op.staged for op in item.operations)
                for item in changes
                if item.status == "committed"
            ):
                return "Часть изменений сохранена"
            return "Изменения сохранены"
        return (
            "Есть предложения"
            if any(item.operations for item in changes)
            else "Анализ завершён без предложений"
        )

    def _render_inbox(self) -> None:
        query = self.query_one("#section-search", Input).value.casefold()
        choices = [
            source
            for source in self.vault.list_sources()
            if bool(source.metadata.get("archived")) == self._show_archived
        ]
        self._source_choices = [
            source
            for source in choices
            if query in self._source_label(source).casefold()
        ]
        self._show_context(
            "АРХИВ ИСТОЧНИКОВ" if self._show_archived else "ИСТОЧНИКИ",
            "Выберите источник стрелками и нажмите Enter."
            if self._source_choices
            else "Источников не найдено.",
            chooser=True,
        )
        self._chooser_mode = "sources"
        self._fill_chooser(
            [
                (
                    self._source_label(source) + "\n" + self._source_status(source.id),
                    source.id,
                )
                for source in self._source_choices
            ]
        )
        self._set_next_actions(
            ("Добавить источник", "/add"), ("Найти диалог", "/discover")
        )
        self._update_navigation()

    def _fill_chooser(self, rows: list[tuple[str, str]]) -> None:
        chooser = self.query_one("#chooser", ListView)
        chooser.clear()
        chooser.extend(
            ListItem(Label(label, markup=False), name=name) for label, name in rows
        )
        self.call_after_refresh(self._focus_list, "chooser", 0)

    def _remove_source_prompt(self) -> None:
        if self._model_busy:
            self._status(
                "Дождитесь завершения анализа перед удалением или архивированием",
                error=True,
            )
            return
        if not self.source_id:
            return
        source = self.vault.get_source(self.source_id)
        impact = self.vault.source_removal_impact(source.id)
        self._delete_confirmation = (source.id, impact["draft_ids"])
        if source.metadata.get("archived"):
            self._show_context(
                "ВОССТАНОВИТЬ ИСТОЧНИК", "Вернуть источник из архива в рабочий список?"
            )
            self._set_next_actions(
                ("Вернуть в источники", "/source-archive restore"), ("Отмена", "/back")
            )
        elif impact["can_delete"]:
            self._show_context(
                "УДАЛИТЬ ИСТОЧНИК",
                f"{escape(self._source_label(source))}\n\nУдалятся локальный снимок, фрагменты и черновики: {len(impact['draft_ids'])}.\nОригинальный файл не изменится. Действие необратимо.",
            )
            self._set_next_actions(
                ("Удалить источник", "/source-delete"), ("Отмена", "/back")
            )
        else:
            self._show_context(
                "ИСТОЧНИК ИСПОЛЬЗУЕТСЯ",
                f"Карточек: {len(impact['card_ids'])} · записей истории: {len(impact['history_ids'])} · общих предложений: {len(impact['shared_draft_ids'])}.\n\nПолное удаление заблокировано, чтобы не сломать цитаты. Архив скроет источник из рабочего списка, сохранив доказательства.",
            )
            self._set_next_actions(
                ("Убрать в архив", "/source-archive"), ("Отмена", "/back")
            )
        self.query_one("#next-primary").focus()

    def _finish_source_removal(self, archive: bool, restore: bool = False) -> None:
        if self._model_busy or self._delete_confirmation is None:
            raise ValueError(
                "Сначала проверьте последствия удаления; во время анализа удаление недоступно"
            )
        source_id, drafts = self._delete_confirmation
        if archive:
            self.vault.archive_source(source_id, archived=not restore)
        else:
            self.vault.delete_source(source_id, expected_drafts=drafts)
        self._delete_confirmation = None
        self.source_id = self.changeset_id = self.operation_id = None
        self._navigate_section("sources")
        self._status(
            "Источник восстановлен"
            if restore
            else "Источник в архиве"
            if archive
            else "Источник удалён; оригинальный файл не изменён"
        )

    def _select_source(self, token: str) -> SourceSnapshot:
        sources = self.vault.list_sources()
        try:
            index = int(token) - 1
        except ValueError:
            source = self.vault.get_source(token)
        else:
            if index < 0 or index >= len(sources):
                raise ValueError("Источник с таким номером не найден")
            source = sources[index]
        self.source_id = source.id
        return source

    def _render_source(self, source: SourceSnapshot) -> None:
        evidence = self.vault.list_evidence(source.id)
        snippets = []
        for item in evidence:
            text = (
                conversation_text(str(item.locator.get("role", "")), item.text)
                if item.locator.get("kind") == "conversation"
                else item.text
            )
            if not text.strip():
                continue
            snippets.append(
                f"[#e5b567]{escape(self._locator(item.locator))}[/]\n"
                + (
                    "[bold]Ответ ИИ · не проверено по первоисточнику[/]\n"
                    if item.trust == "assistant_unverified"
                    else ""
                )
                + f"{escape(text[:400])}"
                + (
                    "\n[#aaaaaa]… предпросмотр фрагмента[/]"
                    if len(item.text) > 400
                    else ""
                )
            )
            if len(snippets) == 4:
                break
        body = (
            f"[bold]{escape(source.title)}[/]  [#aaaaaa]{escape(source.kind)}[/]\n"
            f"[bold]Источник сохранён локально. {self._source_status(source.id)}.[/]\n"
            "Следующий шаг: предложить карточки, затем выбрать, что сохранить.\n\n"
            + (
                "\n\n".join(snippets)
                if snippets
                else "Требуется vision-анализ"
                if any(item.locator.get("needs_vision") for item in evidence)
                else "Нет содержательных сообщений после удаления служебного контекста."
            )
            + "\n\n[#aaaaaa]/propose · /inbox[/]"
        )
        self._show_context("ИСТОЧНИК", body)
        drafts = [
            change
            for change in self.vault.list_changesets(status="open")
            if source.id in change.source_ids and change.operations
        ]
        if drafts:
            self.changeset_id = drafts[0].id
        self._set_next_actions(
            ("Продолжить проверку", "/diff")
            if drafts
            else ("Предложить карточки", "/propose"),
            ("К источникам", "/inbox"),
        )

    def _render_changeset(
        self,
        changeset: Changeset,
        *,
        commit_review: bool = False,
        focus_operations: bool = True,
    ) -> None:
        previous_view = self._capture_view()
        self.changeset_id = changeset.id
        if not changeset.operations:
            self.operation_id = None
            sources = "\n".join(
                escape(self._source_label(self.vault.get_source(sid)))
                for sid in changeset.source_ids
            )
            explanation = (
                "Модель не нашла полезной информации для сохранения в этой попытке.\n"
                "Возможно, в источнике только вопросы, информация уже сохранена или модель её не выделила.\n"
                "Это не означает, что источник бесполезен. Карточки не изменены.\n\n"
                if changeset.model
                else "После удаления служебного контекста нет содержательных сообщений для анализа.\n"
                "Модель не вызывалась. Карточки не изменены.\n\n"
            )
            self._show_context(
                "АНАЛИЗ ЗАВЕРШЁН · НЕТ ПРЕДЛОЖЕНИЙ",
                explanation + f"[bold]Источники анализа:[/]\n{sources}\n\n"
                "Можно повторить анализ тех же источников или выбрать другие.",
                previous_view=previous_view,
            )
            self._set_next_actions(
                ("Повторить анализ", "/retry-propose"), ("К источникам", "/inbox")
            )
            self.call_after_refresh(self._focus_control, "#next-primary")
            return
        operations = self.query_one("#operations", ListView)
        selected_index = operations.index or 0
        operations.clear()
        operations.extend(
            ListItem(
                Label(
                    f"{'[x]' if op.staged else '[ ]'}  "
                    f"{self._operation_label(op.kind)} · {self._operation_title(op)}\n"
                    f"     {self._operation_subtitle(op)}",
                    markup=False,
                ),
                name=op.id,
            )
            for op in changeset.operations
        )
        if changeset.operations:
            known = {op.id for op in changeset.operations}
            if self.operation_id not in known:
                self.operation_id = changeset.operations[0].id
            selected = next(
                op for op in changeset.operations if op.id == self.operation_id
            )
            selected_index = next(
                index
                for index, operation in enumerate(changeset.operations)
                if operation.id == selected.id
            )
        staged = sum(operation.staged for operation in changeset.operations)
        title = (
            f"ПЕРЕД COMMIT · {staged}/{len(changeset.operations)} ВЫБРАНО"
            if commit_review
            else f"ПРЕДЛОЖЕНИЯ · {staged}/{len(changeset.operations)} ВЫБРАНО"
        )
        if commit_review:
            body = (
                "[#f0a06f]Применятся только выбранные изменения, атомарно.[/] "
                "Невыбранные останутся в черновике.\n"
                "↑↓ просмотр · Space выбор · Enter открыть · Ctrl+E правка\n\n"
                "[bold]/commit confirm[/]  [#aaaaaa]/diff raw · Escape[/]"
            )
        else:
            body = (
                "Это черновики: карточки базы пока не изменены.\n"
                "1. Enter: прочитать целиком.  2. Space: выбрать.\n"
                "3. «Проверить выбранное»: подтвердить сохранение.\n"
                "↑↓ перемещение · Ctrl+E правка · Escape назад"
            )
        self._show_context(
            title, body, operations=True, preview=True, previous_view=previous_view
        )
        if changeset.operations:
            self._set_next_actions(
                (
                    ("Сохранить выбранное", "/commit confirm")
                    if commit_review
                    else ("Проверить выбранное", "/commit")
                ),
                ("Вернуться к выбору", "/diff")
                if commit_review
                else ("Редактировать", "/edit"),
            )
            self.query_one("#next-primary", Button).disabled = staged == 0
        else:
            self.query_one("#chooser-preview", Static).update(
                "Новых изменений не предложено. Можно добавить другой источник."
            )
        if changeset.operations:
            selected = changeset.operations[selected_index]
            self._update_operation_preview(changeset, selected)
            self.call_after_refresh(
                self._restore_operation_focus, selected_index, focus_operations
            )

    def _restore_operation_focus(self, index: int, focus: bool) -> None:
        operations = self.query_one("#operations", ListView)
        # Clear/extend mounts asynchronously; restore selection after the new rows exist.
        operations.index = index
        if focus:
            operations.focus()
            if operations.highlighted_child:
                operations.highlighted_child.scroll_visible(animate=False)

    @staticmethod
    def _operation_label(kind: str) -> str:
        return {
            "create_card": "СОЗДАТЬ КАРТОЧКУ",
            "update_card": "ОБНОВИТЬ КАРТОЧКУ",
            "merge_card": "ОБЪЕДИНИТЬ КАРТОЧКИ",
            "archive_card": "АРХИВИРОВАТЬ КАРТОЧКУ",
            "add_relation": "ДОБАВИТЬ СВЯЗЬ",
            "remove_relation": "УДАЛИТЬ СВЯЗЬ",
        }.get(kind, kind.upper())

    @staticmethod
    def _operation_subtitle(operation: ProposalOperation) -> str:
        value = operation.after.get("type") or operation.after.get("relation_type")
        if not isinstance(value, str):
            value = "изменение знания"
        return value

    @staticmethod
    def _operation_title(operation: ProposalOperation) -> str:
        title = operation.after.get("title")
        if isinstance(title, str) and title:
            return title
        return operation.target_id or "новый объект"

    def _operation_preview(
        self, changeset: Changeset, operation: ProposalOperation, *, full: bool = False
    ) -> str:
        lines = [
            f"[bold #e5b567]{escape(self._operation_label(operation.kind))}[/]",
            f"[bold]{escape(self._operation_title(operation))}[/]",
        ]
        content = operation.after.get("content")
        if isinstance(content, str) and content:
            lines.extend(("", escape(content if full else content[:700])))
            if not full and len(content) > 700:
                lines.append("[#aaaaaa]Краткий просмотр. Enter: прочитать целиком.[/]")
        fields = [
            (key, value)
            for key, value in operation.after.items()
            if key not in {"title", "content", "type"}
        ]
        if fields:
            lines.extend(
                (
                    "",
                    "[#aaaaaa]"
                    + escape(" · ".join(f"{key}: {value}" for key, value in fields[:5]))
                    + "[/]",
                )
            )
        if operation.reason:
            lines.extend(("", f"[bold]Почему:[/] {escape(operation.reason)}"))
        evidence_by_id = {
            evidence.id: (source, evidence)
            for source_id in changeset.source_ids
            for source in [self.vault.get_source(source_id)]
            for evidence in self.vault.list_evidence(source_id)
        }
        evidence_item = next(
            (
                evidence_by_id[evidence_id]
                for evidence_id in operation.evidence_ids
                if evidence_id in evidence_by_id
            ),
            None,
        )
        if evidence_item:
            source, evidence = evidence_item
            lines.extend(
                (
                    "",
                    f"[bold]Источник:[/] {escape(source.title)}\n"
                    f"[#e5b567]{escape(self._locator(evidence.locator))}[/]",
                )
            )
        if any(
            evidence_by_id[eid][1].trust == "assistant_unverified"
            for eid in operation.evidence_ids
            if eid in evidence_by_id
        ):
            lines.extend(
                (
                    "",
                    "[bold #e5b567]Содержит сведения из ответа ИИ; не проверено по первоисточнику.[/]",
                )
            )
        return "\n".join(lines)

    def _update_operation_preview(
        self, changeset: Changeset, operation: ProposalOperation
    ) -> None:
        preview = self._operation_preview(changeset, operation)
        self.query_one("#chooser-preview", Static).update(preview)
        body = self._plain_text(str(self.query_one("#context-body", Static).render()))
        self._copy_context_text = self._plain_text(
            f"{self.query_one('#context-title', Static).render()}\n\n{preview}\n\n{body}"
        )

    def _render_cards(self) -> None:
        query = self.query_one("#section-search", Input).value
        cards = (
            [hit.card for hit in self.vault.search(query)]
            if query.strip()
            else self.vault.list_cards()
        )
        self._card_choices = [card.id for card in cards]
        self._show_context(
            "КАРТОЧКИ",
            "Выберите карточку; поиск работает по подтверждённой базе."
            if cards
            else "Подтверждённых карточек не найдено.",
            chooser=True,
        )
        self._chooser_mode = "cards"
        self._fill_chooser(
            [
                (f"{card.title} · v{card.version}\n{card.content[:120]}", card.id)
                for card in cards
            ]
        )
        self._set_next_actions(
            ("Проверить базу", "/garden"), ("Добавить источник", "/add")
        )
        self._update_navigation()

    def _render_card(self, token: str) -> None:
        cards = self.vault.list_cards()
        try:
            card = cards[int(token) - 1]
        except (ValueError, IndexError):
            card = self.vault.get_card(token)
        hit = self.vault.hit(card.id)
        citations = (
            "\n".join(
                f"[#e5b567]{escape(self._locator(item.locator))}[/] · "
                + (
                    "Ответ ИИ · не проверено · "
                    if item.trust == "assistant_unverified"
                    else ""
                )
                + f"{escape(item.snippet[:180])}"
                for item in hit.citations
            )
            or "Нет прикреплённых фрагментов"
        )
        relations = [
            relation
            for relation in self.vault.list_relations()
            if card.id in {relation.from_card_id, relation.to_card_id}
        ]
        relation_text = (
            "\n".join(
                f"→ {escape(item.type)} · {self._short(item.id)}" for item in relations
            )
            or "Связей пока нет"
        )
        body = (
            f"[bold #9ac27a]{escape(card.title)} · v{card.version}[/]\n\n"
            f"{escape(card.content)}\n\n"
            f"[bold]Источники[/]\n{citations}\n\n"
            f"[bold]Связи[/]\n{relation_text}"
        )
        self._show_context("ПОДТВЕРЖДЁННАЯ КАРТОЧКА", body, chooser=True)
        links = [
            (
                f"Источник · {self._source_label(self.vault.get_source(item.source_id))}",
                f"source:{item.source_id}",
            )
            for item in hit.citations
        ]
        for relation in relations:
            related_id = (
                relation.to_card_id
                if relation.from_card_id == card.id
                else relation.from_card_id
            )
            related = self.vault.get_card(related_id)
            links.append((f"Связь · {related.title}", f"card:{related_id}"))
        links = list(dict.fromkeys(links))
        self._card_links = [key for _, key in links]
        self._chooser_mode = "card-links"
        self._fill_chooser(links)

    def _render_model(self) -> None:
        model = self._active_model_name()
        embedding = self._active_embedding_name()
        body = (
            "Настройки действуют до выхода из TUI.\n"
            "Ollama обрабатывает локально. Для облака требуется явное разрешение\n"
            "при запуске: --allow-cloud-sources. Диагностика делает тестовый запрос."
        )
        self._show_context("МОДЕЛЬ И ХРАНИЛИЩЕ", body)
        self.query_one("#settings-form").remove_class("hidden")
        self.query_one("#settings-model", Input).value = model
        self.query_one("#settings-embedding", Input).value = embedding
        self.query_one("#settings-vault-path", Input).value = str(self.vault.root)
        self.query_one("#settings-profile", Select).value = self.profile
        self._set_next_actions(
            ("Сохранить настройки", "/settings-save"), ("Диагностика", "/diagnose")
        )
        self.call_after_refresh(self._focus_control, "#settings-profile")

    def _save_settings(self) -> None:
        if self._model_busy:
            raise ValueError("Дождитесь завершения запроса")
        profile = str(self.query_one("#settings-profile", Select).value)
        if profile == "cloud" and not getattr(self.args, "allow_cloud_sources", False):
            raise ValueError(
                "Для облака перезапустите CLI с --allow-cloud-sources; локальные данные без разрешения не отправляются"
            )
        self.profile = profile
        self.model_name = self.query_one("#settings-model", Input).value.strip() or None
        self.embedding_model = (
            self.query_one("#settings-embedding", Input).value.strip() or None
        )
        self._refresh_header()
        self._status("Настройки применены к этому запуску; данные базы не изменились")

    def _diagnose_model(self) -> None:
        if self._model_busy:
            raise ValueError("Дождитесь завершения запроса")
        self._save_settings()
        settings = self._model_settings()
        self._begin_model_activity("Проверяю модель")
        self._diagnose_worker(settings)

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _diagnose_worker(self, settings: dict) -> None:
        try:
            with OpenAICompatibleProvider(**settings) as provider:
                report = provider.diagnose()
            self.call_from_thread(self._model_success, "diagnose", report)
        except Exception as exc:
            self.call_from_thread(self._model_failed, "diagnose", exc)

    def _render_history(self) -> None:
        self._commit_choices = self.vault.list_commits()
        if not self._commit_choices:
            body = "История пока пуста."
        else:
            body = "\n\n".join(
                f"[bold]{index}. {self._short(item.id)}[/] · "
                f"{item.committed_at:%d.%m.%Y %H:%M}\n"
                f"changeset {self._short(item.changeset_id)}"
                + (
                    f" · revert {self._short(item.inverse_of)}"
                    if item.inverse_of
                    else ""
                )
                for index, item in enumerate(self._commit_choices, 1)
            )
            body += "\n\n[#aaaaaa]/revert N · откат создаёт новый commit[/]"
        self._show_context(
            "ИСТОРИЯ",
            "История изменений базы, не разговоров." if self._commit_choices else body,
            chooser=True,
        )
        self._chooser_mode = "history"
        self._fill_chooser(
            [
                (
                    f"{item.committed_at:%d.%m.%Y %H:%M} · {self._short(item.id)}",
                    item.id,
                )
                for item in self._commit_choices
            ]
        )

    def _with_engine(self, callback: Callable[[KnowledgeEngine], T]) -> T:
        if self.provider_factory:
            return callback(KnowledgeEngine(self.vault, self.provider_factory()))
        settings = self._model_settings()
        with OpenAICompatibleProvider(**settings) as provider:
            return callback(KnowledgeEngine(self.vault, provider))

    def _model_settings(self) -> dict:
        cloud_allowed = bool(getattr(self.args, "allow_cloud_sources", False))
        if self.profile == "cloud" and not cloud_allowed:
            raise PermissionError(
                "Для облачной модели перезапустите Twindex с --allow-cloud-sources"
            )
        return {
            "profile": self.profile,
            "model": self.model_name,
            "embedding_model": self.embedding_model,
            "base_url": getattr(self.args, "base_url", None),
            "allow_cloud_sources": cloud_allowed,
            "vision": getattr(self.args, "vision", None),
            "request_timeout": getattr(self.args, "request_timeout", None),
        }

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _model_worker(
        self, action: str, value: str | list[str] | None, settings: dict
    ) -> None:
        try:
            with Vault.open(self.vault.root) as vault:
                with OpenAICompatibleProvider(**settings) as provider:
                    engine = KnowledgeEngine(vault, provider)
                    result: Changeset | Answer
                    if action == "propose":
                        result = engine.propose(
                            value if isinstance(value, list) else [value or ""]
                        )
                    elif action == "garden":
                        result = engine.garden_scan()
                    else:
                        result = engine.answer(value if isinstance(value, str) else "")
            self.call_from_thread(self._model_success, action, result)
        except VisionUnavailable as exc:
            self.call_from_thread(self._vision_failed, str(exc))
        except Exception as exc:
            self.call_from_thread(self._model_failed, action, exc)

    def _vision_failed(self, message: str) -> None:
        self._end_model_activity()
        source = self.vault.get_source(self._request_source_id or self.source_id or "")
        self._append_assistant(
            "Источник сохранён локально, но анализ остановлен: текущая модель "
            "не умеет распознавать изображения.",
            warning=True,
        )
        self._show_context(
            "НУЖНА VISION-МОДЕЛЬ",
            f"[bold]{escape(source.title)}[/]\n\n"
            "Страницы без текстового слоя не будут пропущены молча, а карточки по "
            "неполному документу не будут предложены.\n\n"
            f"[#aaaaaa]{escape(message)}[/]\n\n"
            "[bold]/model[/]  [#aaaaaa]или оставьте источник в Inbox на потом[/]",
        )
        self._status("Анализ остановлен: нужна vision-модель", error=True)

    def _model_failed(self, action: str, error: Exception) -> None:
        self._end_model_activity()
        self._last_diagnostic_path = None
        try:
            self._last_diagnostic_path = write_model_report(
                self.vault.root, action, self._active_model_name(), error
            )
        except OSError:
            pass
        message = (
            "Модель вернула ответ, который не прошёл проверку формата."
            if type(error).__name__ == "ModelOutputError"
            else "Запрос не завершился. Проверьте доступность модели и настройки подключения."
        )
        self._append_assistant(
            f"{message}\nПодтверждённые карточки не изменены. Подробности: /debug.",
            warning=True,
        )
        if self._section == "chat":
            self._set_next_actions(
                ("Повторить вопрос", "/retry-answer")
                if action == "answer" and self._last_question
                else ("Настройки модели", "/model"),
                ("Диагностика ошибки", "/debug"),
            )
        self._status("Запрос не завершён · /debug: технические детали", error=True)

    def _show_diagnostics(self) -> None:
        body = (
            "Отчёта об ошибке нет. Для проверки подключения откройте настройки модели."
        )
        if self._last_diagnostic_path:
            body = (
                "Локальный отчёт без документов, вопросов, ответов модели и ключей.\n"
                f"Файл: {escape(str(self._last_diagnostic_path))}\n\n"
                + escape(self._last_diagnostic_path.read_text())
            )
        self._show_context("ДИАГНОСТИКА ОШИБКИ", body)
        self._set_next_actions(
            ("Повторить вопрос", "/retry-answer")
            if self._last_question
            else ("Настройки модели", "/model"),
            ("Назад", "/back"),
        )

    def _retry_answer(self) -> None:
        if self._model_busy:
            raise ValueError("Дождитесь завершения текущего запроса")
        if not self._last_question:
            raise ValueError("Нет вопроса для повторного запроса")
        self._navigate_section("chat")
        self._answer(self._last_question)

    def _model_success(self, action: str, result) -> None:
        moved = (
            self._nav_epoch != self._model_origin_epoch and not self.provider_factory
        )
        self._end_model_activity()
        if moved:
            if action == "answer":
                citations = "\n\n".join(
                    f"{escape(self._locator(cite.locator))} · карточка v{cite.card_version}\n{escape(cite.snippet)}"
                    for cite in result.citations
                )
                text = (
                    "Недостаточно подтверждённых данных."
                    if result.insufficient_evidence
                    else escape(result.text)
                )
                self._append_assistant(text + (f"\n\n{citations}" if citations else ""))
            elif action == "diagnose":
                self._append_assistant(
                    "Диагностика:\n"
                    + escape(json.dumps(result, ensure_ascii=False, indent=2))
                )
            self._status(
                "Предложение сохранено в источниках."
                if action == "propose"
                else "Результат готов; откройте разговор."
                if action in {"answer", "diagnose"}
                else "Проверка базы завершена; откройте /diff."
            )
            if action == "garden":
                self.changeset_id = result.id
            self._update_navigation()
            return
        if action == "diagnose":
            self._show_context(
                "ДИАГНОСТИКА", escape(json.dumps(result, ensure_ascii=False, indent=2))
            )
            self._status("Диагностика завершена")
            return
        if action in {"propose", "garden"}:
            self.changeset_id = result.id
            self.operation_id = None
            self._refresh_selection()
            self._append_assistant(
                f"Подготовлено операций: [bold]{len(result.operations)}[/]. "
                "Пока ничего не применено."
            )
            self._render_changeset(result)
            self._status(
                "Выберите изменения стрелками и Space"
                if result.operations
                else "Анализ завершён: сохранять пока нечего"
            )
        else:
            self._render_answer(result)

    def _render_answer(self, answer: Answer) -> None:
        if answer.insufficient_evidence:
            self._append_assistant(
                "Пока не могу ответить надёжно. В подтверждённых карточках "
                "недостаточно данных.",
                warning=True,
            )
        else:
            self._append_assistant(escape(answer.text))
        citations = "\n\n".join(
            f"[#e5b567]{escape(self._locator(cite.locator))}[/] · "
            f"карточка v{cite.card_version}\n{escape(cite.snippet)}"
            for cite in answer.citations
        )
        if citations:
            self._show_context("ИСТОЧНИКИ ОТВЕТА", citations)
        self._status(
            "Недостаточно подтверждённых данных"
            if answer.insufficient_evidence
            else "Ответ основан на подтверждённых карточках"
        )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "chooser":
            self._select_chooser_item(event.index)
            return
        if event.list_view.id != "operations":
            return
        self.operation_id = event.item.name
        changeset = self.vault.get_changeset(self.changeset_id or "")
        operation = next(
            op for op in changeset.operations if op.id == self.operation_id
        )
        self._show_operation(changeset, operation)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.has_class("hidden"):
            return
        if event.list_view.id == "operations" and self.changeset_id:
            self.operation_id = event.item.name if event.item else None
            if self.operation_id:
                changeset = self.vault.get_changeset(self.changeset_id)
                operation = next(
                    item
                    for item in changeset.operations
                    if item.id == self.operation_id
                )
                self._update_operation_preview(changeset, operation)
            return
        if event.list_view.id != "chooser" or self._chooser_mode != "conversations":
            return
        if event.list_view.index is not None:
            self._update_conversation_preview(event.list_view.index)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "section-search":
            self._render_inbox() if self._section == "sources" else self._render_cards()
            return
        if event.input.id != "composer":
            return
        raw = event.value.strip()
        if not raw:
            return
        event.input.value = ""
        if not raw.startswith("/") or raw.startswith("/answer "):
            self._append_user(raw.removeprefix("/answer "))
        self._status("")
        try:
            self._dispatch(raw)
        except Exception as exc:
            self._present_error(exc)
        finally:
            if not self.query_one(
                "#editor", TextArea
            ).has_focus and not event.input.has_class("hidden"):
                event.input.focus()

    @staticmethod
    def _normalize_source_path(value: str) -> str:
        value = value.strip()
        try:
            parts = shlex.split(value)
        except ValueError:
            return value
        # Finder drops shell-quoted paths; never collapse spaces in a plain path.
        return parts[0] if len(parts) == 1 else value

    def _dispatch(self, raw: str) -> None:
        if raw.startswith("/add "):
            self._import(self._normalize_source_path(raw[5:]))
            return
        value = self._normalize_source_path(
            raw.removeprefix("Добавь ").removeprefix("добавь ")
        )
        first_token = value.split(maxsplit=1)[0] if value else ""
        absolute_path = first_token.startswith("/") and (
            "/" in first_token[1:] or "." in first_token[1:]
        )
        if not raw.startswith("/") or absolute_path:
            if (
                raw.startswith(("Добавь ", "добавь "))
                or value.startswith(("http://", "https://", "/", "~/", "./", "../"))
                or Path(value).expanduser().exists()
            ):
                self._import(value)
            else:
                self._answer(raw)
            return
        parts = shlex.split(raw)
        command = parts[0].lower()
        args = parts[1:]
        handlers = {
            "/pick": lambda args: self._start_native_import(
                directory=bool(args and args[0] == "folder")
            ),
            "/back": lambda _: self.action_navigation_back(),
            "/new": lambda _: self._clear_session(),
            "/source-delete": lambda _: self._finish_source_removal(False),
            "/source-archive": lambda args: self._finish_source_removal(
                True, bool(args and args[0] == "restore")
            ),
            "/edit-leave": self._edit_leave,
            "/settings-save": lambda _: self._save_settings(),
            "/diagnose": lambda _: self._diagnose_model(),
            "/debug": lambda _: self._show_diagnostics(),
            "/retry-answer": lambda _: self._retry_answer(),
            "/help": self._command_help,
            "/add": self._command_add,
            "/inbox": lambda _: self._render_inbox(),
            "/sources": lambda _: self._render_inbox(),
            "/source": self._command_source,
            "/propose": self._command_propose,
            "/retry-propose": lambda _: self._retry_propose(),
            "/diff": self._command_diff,
            "/op": self._command_operation,
            "/edit": lambda args: self.action_edit(raw=bool(args and args[0] == "raw")),
            "/toggle-selected": lambda _: self._toggle_selected_detail(),
            "/save": lambda _: self.action_save_edit(),
            "/stage": self._command_stage,
            "/unstage": self._command_unstage,
            "/commit": self._command_commit,
            "/garden": lambda _: self.action_garden(),
            "/search": self._command_search,
            "/answer": self._command_answer,
            "/cards": lambda _: self._render_cards(),
            "/card": self._command_card,
            "/log": lambda _: self.action_history(),
            "/revert": self._command_revert,
            "/discover": self._command_discover,
            "/model": self._command_model,
            "/vault": self._command_vault,
            "/clear": lambda _: self._clear_session(),
        }
        handler = handlers.get(command)
        if not handler:
            raise ValueError(f"Неизвестная команда {command}. Используйте /help")
        handler(args)

    def _command_help(self, args: list[str]) -> None:
        if args and args[0].lower() == "all":
            rows = [
                ("Источники", "/add PATH   /inbox   /discover"),
                ("Предложения", "/propose   /op N   /edit   /stage   /commit"),
                ("Знания", "/cards   /search QUERY   /answer QUESTION   /garden"),
                ("История", "/log   /revert N"),
                ("Настройки", "/model   /vault PATH   /clear"),
            ]
            body = "\n".join(
                f"[bold]{label:<13}[/] {commands}" for label, commands in rows
            )
            body += "\n\n[#aaaaaa]Escape возвращает на предыдущий экран.[/]"
            self._show_context("ВСЕ КОМАНДЫ", body)
            return
        body = (
            "[bold]Добавить источник[/]      [#e5b567]/add PATH[/]\n"
            "[bold]Найти диалоги[/]          [#e5b567]/discover[/]\n"
            "[bold]Открыть Inbox[/]          [#e5b567]/inbox[/]\n"
            "[bold]Спросить по знаниям[/]    просто напишите вопрос\n\n"
            "После импорта: [#e5b567]/propose[/] → проверить изменения → "
            "[#e5b567]/commit[/].\n"
            "Полный справочник: [#e5b567]/help all[/]"
        )
        self._show_context("КАК НАЧАТЬ", body)

    def _command_add(self, args: list[str]) -> None:
        if not args:
            raise ValueError("Укажите путь к файлу, папке или URL")
        self._import(" ".join(args))

    def _import(self, value: str) -> None:
        if self._native_busy:
            raise ValueError("Завершите или отмените системный выбор источника")
        path = Path(value).expanduser()
        ingestor = SourceIngestor(self.vault)
        try:
            if not value.startswith(("http://", "https://")):
                mode = path.stat().st_mode
                if (
                    stat.S_ISREG(mode)
                    and path.suffix.lower() not in SUPPORTED_SOURCE_SUFFIXES
                ):
                    suffix = path.suffix.lower() or "без расширения"
                    raise ValueError(
                        f"Этот файл нельзя импортировать: формат {suffix} пока не "
                        f"поддерживается.\nПоддерживаются: {SUPPORTED_SOURCE_FORMATS}."
                    )
            sources = (
                [ingestor.ingest_url(value)]
                if value.startswith(("http://", "https://"))
                else ingestor.ingest_path(path)
            )
        except FileNotFoundError as exc:
            raise ValueError(
                f"Файл или папка не найдены: {path}\n"
                "Перетащите файл в окно терминала или проверьте путь."
            ) from exc
        except PermissionError as exc:
            if sys.platform == "darwin":
                self._append_assistant(
                    "Для чтения этого источника нужен ваш выбор в системном окне. Выберите файл или папку и нажмите «Прочитать». Отмена ничего не импортирует."
                )
                self._start_native_import(path, directory=not bool(path.suffix))
                return
            message = f"Нет доступа к чтению: {path}\n"
            message += (
                "Проверьте права файла и родительских папок для текущего пользователя."
            )
            raise ValueError(message) from exc
        self._show_imported_sources(sources)

    def _show_imported_sources(self, sources) -> None:
        if not sources:
            raise ValueError("Поддерживаемых источников не найдено")
        self._navigate_section("sources")
        self.source_id = sources[0].id
        self._refresh_selection()
        self._append_assistant(
            f"Источник сохранён локально: [bold]{escape(sources[0].title)}[/].\n"
            "Следующий шаг: проверьте источник и предложите карточки."
        )
        self._render_source(sources[0])
        self.call_after_refresh(self.query_one("#next-primary").focus)
        self._status(f"Импортировано источников: {len(sources)}")
        warnings = list(
            dict.fromkeys(
                warning
                for source in sources
                for warning in source.metadata.get("warnings", [])
            )
        )
        if warnings:
            self._append_assistant(
                "Ограничения импорта:\n" + escape("\n".join(warnings)), warning=True
            )

    def _start_native_import(
        self, target: Path | None = None, *, directory: bool = False
    ) -> None:
        if self._native_busy or self._model_busy:
            raise ValueError("Дождитесь завершения текущей операции")
        if sys.platform != "darwin":
            raise ValueError("Вставьте путь к файлу или папке в строку импорта")
        self._native_busy = True
        self._native_origin_epoch = self._nav_epoch
        self._status("Выберите источник в системном окне macOS · Отмена без импорта")
        self._native_import_worker(target, directory)

    @work(thread=True, group="file-access", exclusive=True, exit_on_error=False)
    def _native_import_worker(self, target: Path | None, directory: bool) -> None:
        try:
            acquired = self._file_picker.pick(target, directory=directory)
            self.call_from_thread(self._native_import_finished, acquired, None)
        except Exception as exc:
            if self.is_running:
                self.call_from_thread(self._native_import_finished, None, str(exc))

    def _native_import_finished(self, acquired, error: str | None) -> None:
        self._native_busy = False
        if error:
            self._append_assistant(
                "Не удалось прочитать выбранный источник.\n"
                + escape(error)
                + "\nМожно выбрать другую локальную копию. Ничего не импортировано.",
                warning=True,
            )
            self._status("Импорт не выполнен; /pick открывает выбор снова", error=True)
            return
        if acquired is None:
            self._status("Выбор отменён; ничего не импортировано")
            return
        try:
            sources = SourceIngestor(self.vault).ingest_acquired(acquired)
            if self._nav_epoch == self._native_origin_epoch:
                self._show_imported_sources(sources)
            else:
                self._status(f"Импортировано: {len(sources)}. Откройте Источники.")
        except Exception as exc:
            self._present_error(exc)

    def _command_source(self, args: list[str]) -> None:
        if not args:
            raise ValueError("Укажите номер источника из /inbox")
        self._render_source(self._select_source(args[0]))

    def _show_current_changeset(self) -> None:
        if not self.changeset_id:
            raise ValueError("Открытых предложений пока нет")
        self._render_changeset(self.vault.get_changeset(self.changeset_id))

    def _command_diff(self, args: list[str]) -> None:
        if not self.changeset_id:
            raise ValueError("Открытых предложений пока нет")
        changeset = self.vault.get_changeset(self.changeset_id)
        if args and args[0].lower() == "raw":
            self._show_context(
                f"ТЕХНИЧЕСКИЙ PATCH · {self._short(changeset.id)}",
                escape(self.vault.diff(changeset.id))
                + "\n\n[#aaaaaa]/diff вернёт к обзору карточек[/]",
            )
            return
        self._render_changeset(changeset)

    def _command_operation(self, args: list[str]) -> None:
        if not self.changeset_id or not args:
            raise ValueError("Используйте /op N после открытия changeset")
        changeset = self.vault.get_changeset(self.changeset_id)
        operation: ProposalOperation | None
        try:
            operation = changeset.operations[int(args[0]) - 1]
        except (ValueError, IndexError):
            operation = next(
                (item for item in changeset.operations if item.id == args[0]), None
            )
            if operation is None:
                raise ValueError("Операция не найдена")
        self.operation_id = operation.id
        self._render_changeset(changeset)

    def _command_stage(self, args: list[str]) -> None:
        self._set_staged(args, True)

    def _command_unstage(self, args: list[str]) -> None:
        self._set_staged(args, False)

    def _set_staged(self, args: list[str], staged: bool) -> None:
        changeset = self.vault.get_changeset(self.changeset_id or "")
        if args and args[0].lower() == "all":
            ids = [item.id for item in changeset.operations]
        elif args:
            ids = []
            for token in args:
                try:
                    ids.append(changeset.operations[int(token) - 1].id)
                except (ValueError, IndexError):
                    ids.append(token)
        elif self.operation_id:
            ids = [self.operation_id]
        else:
            raise ValueError("Выберите операцию через /op N")
        changeset = self.vault.stage(changeset.id, ids, staged=staged)
        self._commit_armed = False
        self._render_changeset(changeset)
        self._status("Операции выбраны" if staged else "Операции исключены")

    def _command_commit(self, args: list[str]) -> None:
        if args and args[0].lower() == "confirm":
            if not self._commit_armed:
                raise ValueError("Сначала откройте проверку командой /commit")
            try:
                self._commit_now()
            except ConflictError as exc:
                self._render_commit_conflict(exc)
            return
        self.action_commit()

    def _render_commit_conflict(self, error: ConflictError) -> None:
        changeset = self.vault.get_changeset(self.changeset_id or "")
        stale = next(
            (
                operation
                for operation in changeset.operations
                if operation.staged and operation.target_id
            ),
            None,
        )
        detail = escape(str(error))
        if stale and stale.kind in {"update_card", "archive_card", "merge_card"}:
            try:
                current = self.vault.get_card(stale.target_id or "")
            except KeyError:
                pass
            else:
                detail = (
                    f"Карточка: [bold]{escape(current.title)}[/]\n"
                    f"Предложение сравнивалось с v{stale.base_version}; "
                    f"сейчас в vault v{current.version}."
                )
        self._commit_armed = False
        self._append_assistant(
            "Не применено: подтверждённое знание изменилось после подготовки "
            "предложения.",
            warning=True,
        )
        self._show_context(
            "КОНФЛИКТ ВЕРСИЙ",
            f"{detail}\n\n"
            f"Changeset [#e5b567]{self._short(changeset.id)}[/] сохранён открытым; "
            "ни одна его операция не применена.\n\n"
            "[#aaaaaa]Откройте актуальную карточку и создайте новое предложение.[/]",
        )
        self._status("Commit остановлен без изменений", error=True)

    def _commit_now(self) -> None:
        commit = self.vault.commit(self.changeset_id or "")
        changeset = self.vault.get_changeset(commit.changeset_id)
        applied = sum(operation.staged for operation in changeset.operations)
        remaining = len(changeset.operations) - applied
        self.commit_id = commit.id
        self._commit_armed = False
        self._refresh_selection()
        self._append_assistant(
            f"Изменения сохранены как commit [bold #9ac27a]{self._short(commit.id)}[/].\n"
            f"Применено операций: {applied}; осталось в черновике: {remaining}."
        )
        self._show_context(
            "ПОДТВЕРЖДЁННОЕ ЗНАНИЕ",
            "Карточки теперь участвуют в поиске и ответах с цитатами.\n\n"
            "[#aaaaaa]/cards · /log · откат создаст новый changeset[/]",
        )
        self._status("Commit сохранён")
        self._set_next_actions(
            ("Открыть карточки", "/cards"), ("Добавить источник", "/add")
        )

    def _command_search(self, args: list[str]) -> None:
        if not args:
            raise ValueError("Введите поисковый запрос")
        self._search(" ".join(args))

    def _search(self, query: str) -> None:
        hits = self.vault.search(query)
        if not hits:
            body = "Подтверждённых совпадений не найдено."
        else:
            chunks = []
            for hit in hits:
                citations = "\n".join(
                    f"  [#e5b567]{escape(self._locator(item.locator))}[/] · "
                    f"{escape(item.snippet[:160])}"
                    for item in hit.citations
                )
                chunks.append(
                    f"[bold]{escape(hit.card.title)} · v{hit.card.version}[/]\n"
                    f"{citations}"
                )
            body = "\n\n".join(chunks)
        self._show_context("ПОИСК · ПОДТВЕРЖДЁННЫЕ КАРТОЧКИ", body)

    def _command_answer(self, args: list[str]) -> None:
        if not args:
            raise ValueError("Введите вопрос")
        self._answer(" ".join(args))

    def _answer(self, question: str) -> None:
        if not question:
            raise ValueError("Введите вопрос")
        self._last_question = question
        if not self.provider_factory:
            if self._model_busy:
                raise RuntimeError("Другой запрос к модели ещё выполняется")
            settings = self._model_settings()
            self._begin_model_activity("Модель ищет ответ")
            self._model_worker("answer", question, settings)
            return
        try:
            self._render_answer(
                self._with_engine(lambda engine: engine.answer(question))
            )
        except Exception as exc:
            self._model_failed("answer", exc)

    def _command_card(self, args: list[str]) -> None:
        if not args:
            raise ValueError("Укажите номер карточки из /cards")
        self._render_card(args[0])

    def _command_revert(self, args: list[str]) -> None:
        if not args:
            self._render_history()
            return
        if args[0].lower() == "confirm":
            if not self._revert_armed:
                raise ValueError("Сначала выберите commit командой /revert N")
            reverted = self.vault.revert(self._revert_armed)
            self._revert_armed = None
            self.commit_id = reverted.id
            self._refresh_selection()
            self._append_assistant(
                f"Откат сохранён новым commit [bold]{self._short(reverted.id)}[/]. "
                "История не стиралась."
            )
            self._render_history()
            return
        commits = self.vault.list_commits()
        try:
            commit = commits[int(args[0]) - 1]
        except (ValueError, IndexError):
            commit = self.vault.get_commit(args[0])
        self._revert_armed = commit.id
        self._show_context(
            "ПРОВЕРКА ОТКАТА",
            f"Будет создан новый changeset, обратный commit "
            f"[bold]{self._short(commit.id)}[/].\n"
            "История и исходный commit сохранятся.\n\n"
            "[bold]/revert confirm[/]  [#aaaaaa]/log[/]",
        )
        self._set_next_actions(
            ("Подтвердить откат", "/revert confirm"), ("Отмена", "/back")
        )

    def _command_discover(self, args: list[str]) -> None:
        from .cli import _consent

        mode = args[0].lower() if args else ""
        if mode == "revoke":
            _consent(self.vault, False)
            self._show_context(
                "ДИАЛОГИ",
                "Разрешение на просмотр метаданных отозвано.\n"
                "Никакого фонового сканирования нет.",
            )
            return
        if mode == "grant":
            _consent(self.vault, True)
        if not _consent(self.vault):
            self._render_discovery_consent()
            return
        self._render_discovered_conversations()

    def _render_discovery_consent(self) -> None:
        chooser = self.query_one("#chooser", ListView)
        chooser.clear()
        chooser.extend(
            [
                ListItem(Label("Разрешить и показать список", markup=False)),
                ListItem(Label("Отмена", markup=False)),
            ]
        )
        chooser.index = 0
        self._chooser_mode = "discovery-consent"
        self._show_context(
            "Найти диалоги Claude Code и Codex?",
            "Twindex просмотрит известные каталоги и прочитает короткую первую "
            "фразу каждого диалога, чтобы список был понятным.\n"
            "Ничего не импортируется и не отправляется в сеть.\n\n"
            "[#aaaaaa]↑↓ выбрать · Enter продолжить · Esc отменить[/]",
            chooser=True,
        )
        self.call_after_refresh(chooser.focus)

    def _render_discovered_conversations(self) -> None:
        found = discover_conversations(default_conversation_roots(), consent=True)
        self._conversation_choices = found[:50]
        self._conversation_previews = {
            item.path: conversation_preview(item.path)
            for item in self._conversation_choices
        }
        if not self._conversation_choices:
            self._chooser_mode = None
            self._show_context(
                "ДИАЛОГИ НЕ НАЙДЕНЫ",
                "В известных каталогах Claude Code и Codex нет JSONL-диалогов.\n\n"
                "Можно добавить файл напрямую: [#e5b567]/add PATH[/].",
            )
            return
        chooser = self.query_one("#chooser", ListView)
        chooser.clear()
        chooser.extend(
            ListItem(
                Label(
                    f"{conversation_origin(item.path)} · "
                    f"{self._conversation_previews[item.path]}\n"
                    f"{self._format_size(item.size_bytes)} · "
                    f"{datetime.fromtimestamp(item.modified_at).astimezone():%d.%m.%Y %H:%M}",
                    markup=False,
                )
            )
            for item in self._conversation_choices
        )
        chooser.index = 0
        self._chooser_mode = "conversations"
        self._show_context(
            "ВЫБЕРИТЕ ДИАЛОГ",
            "[#aaaaaa]↑↓ выбрать · Enter импортировать · Esc назад[/]",
            chooser=True,
            preview=True,
        )
        self._update_conversation_preview(0)
        self.call_after_refresh(chooser.focus)

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        if size_bytes < 1024:
            return f"{size_bytes} Б"
        if size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.0f} КБ"
        return f"{size_bytes / (1024 * 1024):.1f} МБ"

    def _update_conversation_preview(self, index: int) -> None:
        if index < 0 or index >= len(self._conversation_choices):
            return
        item = self._conversation_choices[index]
        origin = conversation_origin(item.path)
        preview = self._conversation_previews[item.path]
        self.query_one("#chooser-preview", Static).update(
            f"[bold #e5b567]{escape(origin)}[/]\n"
            f"{escape(preview)}\n"
            f"[#aaaaaa]{escape(item.path.name)}[/]"
        )
        self._copy_context_text = self._plain_text(
            f"ВЫБЕРИТЕ ДИАЛОГ\n\n{origin}\n{preview}\n{item.path}"
        )

    def _select_chooser_item(self, index: int) -> None:
        if self._chooser_mode == "sources":
            if 0 <= index < len(self._source_choices):
                source = self._source_choices[index]
                self.source_id = source.id
                self._render_source(source)
            return
        if self._chooser_mode == "cards":
            if 0 <= index < len(self._card_choices):
                self._render_card(self._card_choices[index])
            return
        if self._chooser_mode == "card-links":
            if 0 <= index < len(self._card_links):
                kind, identifier = self._card_links[index].split(":", 1)
                if kind == "source":
                    self.source_id = identifier
                    self._render_source(self.vault.get_source(identifier))
                else:
                    self._render_card(identifier)
            return
        if self._chooser_mode == "history":
            if 0 <= index < len(self._commit_choices):
                commit = self._commit_choices[index]
                self.commit_id = commit.id
                self._show_context(
                    "ИЗМЕНЕНИЯ COMMIT", escape(self.vault.diff(commit.changeset_id))
                )
                self._set_next_actions(
                    ("Подготовить отмену", f"/revert {commit.id}"), ("Назад", "/back")
                )
            return
        if self._chooser_mode == "discovery-consent":
            if index == 0:
                from .cli import _consent

                _consent(self.vault, True)
                self._render_discovered_conversations()
            else:
                self._hide_context()
                self.action_focus_composer()
            return
        if self._chooser_mode == "conversations":
            if 0 <= index < len(self._conversation_choices):
                path = self._conversation_choices[index].path
                self._chooser_mode = None
                self._import(str(path))
            return

    def _command_model(self, args: list[str]) -> None:
        if not args:
            self._render_model()
            return
        profile = args[0].lower()
        if profile not in {"ollama", "cloud", "custom"}:
            raise ValueError("Профиль должен быть ollama, cloud или custom")
        if profile == "cloud" and not bool(
            getattr(self.args, "allow_cloud_sources", False)
        ):
            raise PermissionError(
                "Облачная передача не разрешена. Перезапустите с --allow-cloud-sources"
            )
        self.profile = profile
        self.model_name = args[1] if len(args) > 1 else None
        self.embedding_model = args[2] if len(args) > 2 else None
        self._refresh_header()
        self._render_model()
        self._status("Модель выбрана; доступность проверьте командой diagnose в CLI")

    def _command_vault(self, args: list[str]) -> None:
        if not args:
            raise ValueError("Укажите каталог vault")
        self._open_vault(Path(" ".join(args)).expanduser())

    def _open_vault(self, path: Path) -> None:
        if self._model_busy or self._native_busy:
            raise RuntimeError("Дождитесь завершения запроса к модели")
        replacement = Vault.open(path)
        self.vault.close()
        self.vault = replacement
        self.source_id = self.changeset_id = self.operation_id = self.commit_id = None
        self._refresh_header()
        self._messages.clear()
        self._last_question = ""
        self._last_diagnostic_path = None
        self._navigate_section("chat")
        self._status(f"Открыт vault {self.vault.root}")

    def _clear_session(self) -> None:
        if self._model_busy or self._native_busy:
            self._status(
                "Дождитесь завершения запроса перед новым разговором", error=True
            )
            return
        if self._guard_editor("new"):
            return
        self._messages.clear()
        self._last_question = ""
        self._last_diagnostic_path = None
        self.source_id = self.changeset_id = self.operation_id = self.commit_id = None
        self._navigate_section("chat")

    def action_focus_composer(self) -> None:
        chooser = self.query_one("#chooser", ListView)
        if self._chooser_mode and chooser.has_focus:
            self._hide_context()
        self.query_one("#composer", Input).focus()

    def action_copy_text(self) -> None:
        focused = self.focused
        text = (
            focused.selected_text
            if isinstance(focused, (Input, TextArea))
            else self.screen.get_selected_text()
        )
        if not text:
            text = (
                self._copy_context_text
                if not self.query_one("#context-panel").has_class("hidden")
                else self._plain_text("\n\n".join(self._messages))
            )
            if not text and not self.query_one("#welcome-intro").has_class("hidden"):
                text = self._plain_text(
                    str(self.query_one("#welcome-copy", Static).render())
                )
        if not text:
            return
        self.copy_to_clipboard(text)
        if sys.platform == "darwin":
            try:
                subprocess.run(
                    ["pbcopy"],
                    input=text,
                    text=True,
                    check=True,
                    timeout=2,
                )
            except (OSError, subprocess.SubprocessError):
                self._status("Не удалось открыть системный буфер обмена", error=True)
                return
        self._status("Скопировано в буфер обмена")

    def _present_error(self, error: Exception) -> None:
        self._append_assistant(
            f"Не удалось выполнить действие.\n[default]{escape(str(error))}[/]",
            warning=True,
        )
        self._status("")

    def _action_failed(self, message: str) -> None:
        self._append_assistant(
            f"Действие пока недоступно.\n[#aaaaaa]{escape(message)}[/]",
            warning=True,
        )
        self._status("")

    def _command_propose(self, args: list[str]) -> None:
        if args:
            raise ValueError(
                "В TUI /propose анализирует выбранный источник, без аргументов. "
                "Для нескольких источников в терминале: twindex propose SOURCE_ID1 SOURCE_ID2"
            )
        self.action_propose()

    def action_propose(self) -> None:
        if not self.source_id:
            self._navigate_section("sources")
            self._status(
                "Сначала выберите источник стрелками и Enter; карточку выбирать не нужно"
            )
            return
        self._propose_sources([self.source_id])

    def _retry_propose(self) -> None:
        if not self.changeset_id:
            self.action_propose()
            return
        change = self.vault.get_changeset(self.changeset_id)
        self._propose_sources(change.source_ids)

    def _propose_sources(self, source_ids: list[str]) -> None:
        if self._model_busy or self._native_busy:
            self._status("Дождитесь завершения текущего запроса")
            return
        try:
            if not source_ids:
                raise ValueError("Выберите хотя бы один источник")
            self._request_source_id = source_ids[0]
            if not self.provider_factory:
                settings = self._model_settings()
                self._begin_model_activity("Модель анализирует источник")
                self._request_source_id = source_ids[0]
                self._model_worker("propose", source_ids, settings)
                return
            changeset = self._with_engine(lambda engine: engine.propose(source_ids))
            self._model_success("propose", changeset)
        except VisionUnavailable as exc:
            self._vision_failed(str(exc))
        except Exception as exc:
            self._model_failed("propose", exc)

    def _show_operation(
        self, changeset: Changeset, operation: ProposalOperation
    ) -> None:
        self.operation_id = operation.id
        selected = (
            "Выбрана для сохранения"
            if operation.staged
            else "Не выбрана для сохранения"
        )
        self._show_context(
            "ПРЕДЛОЖЕННАЯ КАРТОЧКА",
            f"[bold]{selected}[/] · пока черновик\n"
            "Полный текст ниже. PgUp/PgDn или колесо: прокрутка.\n\n"
            + self._operation_preview(changeset, operation, full=True),
        )
        self._set_next_actions(
            (
                "Убрать из выбора" if operation.staged else "Выбрать для сохранения",
                "/toggle-selected",
            ),
            ("Редактировать", "/edit"),
        )
        self._status("После выбора нажмите «Проверить и сохранить» внизу")
        self.call_after_refresh(self.query_one("#timeline").focus)

    def _toggle_selected_detail(self) -> None:
        change = self.vault.get_changeset(self.changeset_id or "")
        operation = next(op for op in change.operations if op.id == self.operation_id)
        change = self.vault.stage(
            change.id, [operation.id], staged=not operation.staged
        )
        self._commit_armed = False
        self._show_operation(
            change, next(op for op in change.operations if op.id == self.operation_id)
        )

    def _editor_snapshot(self) -> str:
        return json.dumps(
            {
                "text": self.query_one("#editor", TextArea).text,
                "fields": {
                    key: self.query_one(f"#edit-{key}", Input).value
                    for key in ("title", "type", "target")
                },
            },
            ensure_ascii=False,
        )

    def _edited_after(self) -> dict:
        text = self.query_one("#editor", TextArea).text
        if self._editor_mode == "raw":
            result = json.loads(text)
            if not isinstance(result, dict):
                raise ValueError("Ожидается JSON-объект")
            return result
        values = {
            "title": self.query_one("#edit-title", Input).value.strip(),
            "type": self.query_one("#edit-type", Input).value.strip(),
            "content": text,
        }
        if self._editor_mode == "relation":
            values = {
                "from_card_id": values["title"],
                "to_card_id": self.query_one("#edit-target", Input).value.strip(),
                "type": values["type"],
                "note": text,
            }
        required = (
            ("title", "type", "content")
            if self._editor_mode == "card"
            else ("from_card_id", "to_card_id", "type")
        )
        if any(not values[key].strip() for key in required):
            raise ValueError("Заполните обязательные поля; правки остаются в редакторе")
        result = self._edit_base.copy()
        for key, value in values.items():
            if value != self._edit_initial.get(key) or key in result:
                result[key] = value
        return result

    def action_edit(self, raw: bool = False) -> None:
        try:
            changeset = self.vault.get_changeset(self.changeset_id or "")
            if not changeset.operations or not self.operation_id:
                raise ValueError("Выберите предложение для редактирования")
            selected = next(
                op for op in changeset.operations if op.id == self.operation_id
            )
            self._editor_mode = (
                "raw"
                if raw or selected.kind in {"archive_card", "remove_relation"}
                else "relation"
                if selected.kind == "add_relation"
                else "card"
            )
            self._edit_base = selected.after.copy()
            values = dict(selected.before or {})
            if selected.kind in {"update_card", "merge_card"} and selected.target_id:
                values.update(self.vault.get_card(selected.target_id).model_dump())
            values.update(selected.after)
            self._edit_initial = values.copy()
            self._show_context(
                "РЕДАКТИРОВАНИЕ ПРЕДЛОЖЕНИЯ",
                "Правки меняют только черновик. Создание карточки требует отдельного подтверждения.\n"
                + (
                    "Технический режим JSON."
                    if self._editor_mode == "raw"
                    else "Название и текст редактируются отдельно. Переносы строк сохраняются."
                ),
                editor=True,
            )
            relation = self._editor_mode == "relation"
            self.query_one("#edit-title-label", Label).update(
                "Откуда ведёт связь · ID карточки" if relation else "Название"
            )
            self.query_one("#edit-content-label", Label).update(
                "Примечание к связи" if relation else "Текст карточки"
            )
            self.query_one("#edit-title", Input).value = str(
                values.get("from_card_id" if relation else "title", "")
            )
            self.query_one("#edit-type", Input).value = str(values.get("type", ""))
            self.query_one("#edit-target", Input).value = str(
                values.get("to_card_id", "")
            )
            for key in ("edit-target", "edit-target-label"):
                self.query_one(f"#{key}").set_class(not relation, "hidden")
            editor = self.query_one("#editor", TextArea)
            editor.text = (
                json.dumps(selected.after, ensure_ascii=False, indent=2)
                if self._editor_mode == "raw"
                else str(values.get("note" if relation else "content") or "")
            )
            self._editor_original = self._editor_snapshot()
            self.call_after_refresh(self._focus_control, "#editor")
            self._set_next_actions(("Сохранить правки", "/save"), ("Назад", "/back"))
            self._status(
                "Tab: следующее поле · Esc: назад с проверкой несохранённых правок"
            )
        except (KeyError, ValueError) as exc:
            self._action_failed(str(exc))

    def action_save_edit(self) -> None:
        changeset = self.vault.get_changeset(self.changeset_id or "")
        if not self.operation_id:
            raise ValueError("Операция не выбрана")
        changeset = self.vault.edit(
            changeset.id, self.operation_id, self._edited_after()
        )
        self._editor_original = self._editor_snapshot()
        self._commit_armed = False
        self.query_one("#editor").add_class("hidden")
        self.query_one("#edit-fields").add_class("hidden")
        # Return to current data, not a saved pre-edit preview.
        self._view_stack = [
            frame for frame in self._view_stack if frame["hidden"]["editor"]
        ]
        if (
            self._view_stack
            and self._view_stack[-1]["title"] == "ПРЕДЛОЖЕННАЯ КАРТОЧКА"
        ):
            self._view_stack.pop()
        self._restoring_view = True
        try:
            self._show_operation(
                changeset,
                next(op for op in changeset.operations if op.id == self.operation_id),
            )
        finally:
            self._restoring_view = False
        self._status("Правки сохранены в предложении; база ещё не изменена")

    def _edit_leave(self, args: list[str]) -> None:
        if self._dirty_destination is None or not args:
            raise ValueError("Нет неподтверждённых правок")
        if args[0] == "save":
            self.vault.edit(
                self.changeset_id or "",
                self.operation_id or "",
                self._edited_after(),
            )
        destination = self._dirty_destination
        self._dirty_destination = None
        self._editor_original = self._editor_snapshot()
        if self._view_stack:
            self._view_stack.pop()  # Remove the editor saved behind its confirmation.
        if destination == "back":
            self.action_navigation_back()
            if self.changeset_id and not self.query_one("#operations").has_class(
                "hidden"
            ):
                self._render_changeset(self.vault.get_changeset(self.changeset_id))
        elif destination == "new":
            self._clear_session()
        else:
            self._navigate_section(destination)

    def action_stage(self) -> None:
        try:
            self._set_staged([], True)
        except (KeyError, ValueError) as exc:
            self._action_failed(str(exc))

    def action_unstage(self) -> None:
        try:
            self._set_staged([], False)
        except (KeyError, ValueError) as exc:
            self._action_failed(str(exc))

    def action_toggle_operation(self) -> None:
        operations = self.query_one("#operations", ListView)
        if operations.has_class("hidden") or not operations.has_focus:
            return
        try:
            changeset = self.vault.get_changeset(self.changeset_id or "")
            operation = next(
                item for item in changeset.operations if item.id == self.operation_id
            )
            changeset = self.vault.stage(
                changeset.id, [operation.id], staged=not operation.staged
            )
            self._commit_armed = False
            self._render_changeset(changeset)
            selected = sum(item.staged for item in changeset.operations)
            self._status(
                f"Выбрано карточек и связей: {selected}/{len(changeset.operations)}"
            )
        except (KeyError, StopIteration, ValueError) as exc:
            self._action_failed(str(exc))

    def action_commit(self) -> None:
        try:
            changeset = self.vault.get_changeset(self.changeset_id or "")
            if not any(operation.staged for operation in changeset.operations):
                raise ValueError(
                    "Выберите хотя бы одну карточку: Space в списке или «Выбрать для сохранения» в просмотре"
                )
            self._commit_armed = True
            self._render_changeset(changeset, commit_review=True)
            self._status("Проверьте выбор и нажмите «Сохранить выбранное»")
        except (KeyError, ValueError) as exc:
            self._action_failed(str(exc))

    def action_garden(self) -> None:
        try:
            if not self.provider_factory:
                if self._model_busy:
                    raise RuntimeError("Другой запрос к модели ещё выполняется")
                settings = self._model_settings()
                self._begin_model_activity("Модель ищет дубли и противоречия")
                self._model_worker("garden", None, settings)
                return
            changeset = self._with_engine(lambda engine: engine.garden_scan())
            self._model_success("garden", changeset)
        except Exception as exc:
            self._model_failed("garden", exc)

    def action_history(self) -> None:
        self._render_history()


def run_tui(vault_path: Path, args=None) -> None:
    TwindexApp(vault_path, args=args).run()
