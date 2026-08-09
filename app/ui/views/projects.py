"""Projects: the workspace, one card per project."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from app.ui import theme
from app.ui.i18n import translator as t
from app.ui.icons import icon as make_icon
from app.ui.views.base import DataView
from app.ui.widgets import ElidedLabel, badge, button, label

if TYPE_CHECKING:  # pragma: no cover
    from app.ui.shell import MainWindow

# .grid-2: cards reflow at this width rather than being squeezed.
CARD_MIN_WIDTH = 320
CARD_MAX_WIDTH = 360
STATUS_TONES = {
    "succeeded": "ok",
    "running": "info",
    "pending": "info",
    "failed": "err",
    "cancelled": "warn",
}


class ProjectDialog(QDialog):
    """Collects the fields the API needs to create a project."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self.window = window
        self.setWindowTitle(t("project.create"))
        self.setMinimumWidth(520)

        self.name = QLineEdit()
        self.location = QLineEdit()
        self.kind = QComboBox()
        self.kind.addItem(t("project.sourceLocal"), "local")
        self.kind.addItem(t("project.sourceGit"), "git")
        self.ref = QLineEdit()

        form = QFormLayout(self)
        form.setSpacing(theme.S[3])
        form.addRow(t("project.name"), self.name)
        form.addRow(t("project.sourceKind"), self.kind)
        form.addRow(t("project.location"), self.location)
        form.addRow("", button(t("project.browse"), icon_name="folder", on_click=self._browse))
        form.addRow(t("project.ref"), self.ref)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse(self) -> None:
        chosen = self.window.pick_folder()
        if chosen:
            self.location.setText(chosen)

    def payload(self) -> dict:
        return {
            "name": self.name.text().strip(),
            "source_kind": self.kind.currentData(),
            "source_location": self.location.text().strip(),
            "default_ref": self.ref.text().strip(),
        }


class ProjectCard(QFrame):
    """One project: where it lives, when it last ran, and what can be done."""

    def __init__(self, project: dict, view: "ProjectsView", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setMinimumWidth(CARD_MIN_WIDTH)
        self.setMaximumWidth(CARD_MAX_WIDTH)
        self.setMinimumHeight(168)
        tokens = view.window.palette_tokens

        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.S[5], theme.S[5], theme.S[5], theme.S[5])
        layout.setSpacing(theme.S[2])

        latest = project.get("latest_analysis") or {}
        head = QHBoxLayout()
        head.setSpacing(theme.S[2])
        glyph = label("")
        glyph.setPixmap(make_icon("folder", tokens.text_2, 16).pixmap(16, 16))
        head.addWidget(glyph)
        head.addWidget(label(str(project.get("name", "")), role="h3"))
        head.addStretch(1)
        if latest:
            status = str(latest.get("status") or "")
            head.addWidget(badge(t(f"status.{status}"), STATUS_TONES.get(status, "info"), tokens))
        layout.addLayout(head)

        source = ElidedLabel(f"{project.get('source_kind', '')}: {project.get('source_location', '')}")
        source.setProperty("role", "muted")
        source.setStyleSheet(f"font-family: {theme.MONO_STACK}; color: {tokens.text_2};")
        layout.addWidget(source)

        when = str(latest.get("finished_at") or latest.get("created_at") or "")
        layout.addWidget(
            label(t("project.lastRun") + ": " + (when[:19].replace("T", " ") if when else t("project.neverRun")), role="muted")
        )
        layout.addStretch(1)

        actions = QHBoxLayout()
        actions.setSpacing(theme.S[2])
        actions.addWidget(
            button(
                t("project.analyze"),
                variant="primary",
                icon_name="play",
                on_click=lambda: view.start(project),
            )
        )
        actions.addWidget(button(t("project.open"), on_click=lambda: view.open(project)))
        actions.addStretch(1)
        remove = button("", variant="ghost", icon_name="trash", colour=tokens.danger, tooltip=t("common.delete"))
        remove.clicked.connect(lambda: view.delete(project))
        remove.setFixedWidth(36)
        actions.addWidget(remove)
        layout.addLayout(actions)


class ProjectsView(DataView):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window, "nav.projects", "project.subtitle")
        self._projects: list[dict] = []

        self.add_header_action(
            button(t("project.create"), variant="primary", icon_name="plus", on_click=self._create)
        )

        self._host = QWidget()
        self._grid = QGridLayout(self._host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(theme.S[4])
        self.add(self._host)

        self._empty = label(t("project.empty"), role="muted")
        self.add(self._empty)
        self.add_stretch()

    def load(self) -> None:
        self.fetch(self.api.projects_list, on_done=self._fill)

    def _columns(self) -> int:
        available = max(1, self.viewport().width() - 2 * theme.S[6])
        return max(1, available // (CARD_MIN_WIDTH + theme.S[4]))

    def _fill(self, projects: Any) -> None:
        self._projects = list(projects or [])
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

        columns = self._columns()
        for position, project in enumerate(self._projects):
            self._grid.addWidget(
                ProjectCard(project, self), position // columns, position % columns
            )
        # A trailing elastic column absorbs the space, so a lone card keeps its
        # grid width instead of stretching the width of the page.
        for column in range(columns):
            self._grid.setColumnStretch(column, 0)
        self._grid.setColumnStretch(columns, 1)
        self._empty.setVisible(not self._projects)

    # --------------------------------------------------------------- actions
    def start(self, project: dict) -> None:
        self.fetch(
            self.api.analysis_start,
            {"project_id": project.get("id")},
            on_done=lambda _: self.window.navigate("analyses"),
        )

    def open(self, project: dict) -> None:
        """Make this the selected project everywhere, then show its analyses."""
        index = self.window.project_picker.findData(project.get("id"))
        if index >= 0:
            self.window.project_picker.setCurrentIndex(index)
        self.window.navigate("analyses")

    def delete(self, project: dict) -> None:
        confirm = QMessageBox.question(
            self, t("project.deleteConfirm"), str(project.get("name", ""))
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.fetch(
            self.api.project_delete, {"project_id": project.get("id")}, on_done=lambda _: self.refresh()
        )

    def _create(self) -> None:
        dialog = ProjectDialog(self.window)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        if not payload["name"] or not payload["source_location"]:
            self.show_error(t("common.required"))
            return
        self.fetch(self.api.project_create, payload, on_done=lambda _: self.refresh())
