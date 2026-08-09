"""The command palette: every destination and action from the keyboard."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from app.ui import theme
from app.ui.i18n import translator as t
from app.ui.icons import icon as make_icon

if TYPE_CHECKING:  # pragma: no cover
    from app.ui.shell import MainWindow


@dataclass(frozen=True)
class Command:
    """One entry: what it is called, where it lives and what it does."""

    label: str
    group: str
    icon: str
    run: Callable[[], None]


class CommandPalette(QDialog):
    """A filtered list over the window's own navigation and actions."""

    def __init__(self, window: "MainWindow", commands: list[Command]) -> None:
        super().__init__(window)
        self._commands = commands
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setModal(True)
        self.setFixedWidth(620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.S[3], theme.S[3], theme.S[3], theme.S[3])
        layout.setSpacing(theme.S[2])

        self.query = QLineEdit()
        self.query.setPlaceholderText(t("palette.placeholder"))
        self.query.textChanged.connect(self._filter)
        self.query.installEventFilter(self)
        layout.addWidget(self.query)

        self.list = QListWidget()
        self.list.setMinimumHeight(320)
        self.list.itemActivated.connect(lambda _: self._accept_current())
        layout.addWidget(self.list)

        self._filter("")

    def _filter(self, text: str) -> None:
        needle = text.strip().lower()
        self.list.clear()
        tokens = self.parent().palette_tokens  # type: ignore[union-attr]
        for command in self._commands:
            if needle and needle not in command.label.lower() and needle not in command.group.lower():
                continue
            entry = QListWidgetItem(f"{command.label}    ·  {command.group}")
            entry.setIcon(make_icon(command.icon, tokens.text_2, 16))
            entry.setData(Qt.ItemDataRole.UserRole, command)
            self.list.addItem(entry)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _accept_current(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        command = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
        if isinstance(command, Command):
            command.run()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        # Arrows and Enter belong to the list even while the field has focus.
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                row = self.list.currentRow() + (1 if key == Qt.Key.Key_Down else -1)
                self.list.setCurrentRow(max(0, min(row, self.list.count() - 1)))
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._accept_current()
                return True
        return super().eventFilter(watched, event)


def build_commands(window: "MainWindow") -> list[Command]:
    """Every navigation destination, plus the actions worth reaching directly."""
    from app.ui.shell import NAVIGATION

    commands = [
        Command(t(item.label_key), t("palette.navigation"), item.icon, lambda k=item.key: window.navigate(k))
        for group in NAVIGATION
        for item in group.items
    ]
    commands += [
        Command(t("analysis.start"), t("palette.actions"), "play", window._start_analysis),
        Command(t("common.theme"), t("palette.actions"), "sparkle", window.toggle_theme),
        Command(t("a11y.toggleSidebar"), t("palette.actions"), "sidebar", window.toggle_sidebar),
        Command(t("common.refresh"), t("palette.actions"), "refresh", window.refresh_current),
    ]
    return commands
