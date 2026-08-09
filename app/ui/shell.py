"""The application window: brand plate, navigation rail and the view area."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app import branding
from app.desktop.bridge import Api
from app.ui import motion
from app.ui import prefs as prefs_store
from app.ui import theme
from app.ui.i18n import translator as t
from app.ui.icons import icon as make_icon
from app.ui.widgets import SearchField, label

log = logging.getLogger("aai.ui.shell")


@dataclass(frozen=True)
class NavItem:
    """One entry in the navigation rail."""

    key: str
    icon: str
    label_key: str
    needs_analysis: bool = False
    needs_score: bool = False


@dataclass(frozen=True)
class NavGroup:
    title_key: str
    items: tuple[NavItem, ...]


# Mirrors the navigation the interface has always had, in the same order.
NAVIGATION: tuple[NavGroup, ...] = (
    NavGroup("nav.groupOverview", (NavItem("dashboard", "dashboard", "nav.dashboard"),)),
    NavGroup(
        "nav.groupWorkspace",
        (
            NavItem("projects", "folder", "nav.projects"),
            NavItem("analyses", "play", "nav.analyses"),
        ),
    ),
    NavGroup(
        "nav.groupQuality",
        (
            NavItem("scorecard", "gauge", "nav.scorecard", needs_score=True),
            NavItem("roadmap", "sparkle", "nav.roadmap", needs_score=True),
            NavItem("hotspots", "alert", "nav.hotspots", needs_analysis=True),
            NavItem("fixes", "wrench", "nav.fixes", needs_analysis=True),
            NavItem("trends", "chart", "nav.trends"),
        ),
    ),
    NavGroup("nav.groupVisual", (NavItem("diagrams", "layers", "nav.diagrams", needs_analysis=True),)),
    NavGroup(
        "nav.groupIntel",
        (
            NavItem("insights", "report", "nav.insights", needs_analysis=True),
            NavItem("history", "history", "nav.history", needs_analysis=True),
            NavItem("compare", "compare", "nav.compare"),
        ),
    ),
    NavGroup(
        "nav.groupSystem",
        (
            NavItem("settings", "settings", "nav.settings"),
            NavItem("about", "info", "nav.about"),
        ),
    ),
)


class MainWindow(QMainWindow):
    """Hosts the views and owns everything global: theme, language, routing."""

    analysis_changed = Signal(object)

    def __init__(self, api: Api, preferences: prefs_store.Preferences) -> None:
        super().__init__()
        self.api = api
        self.prefs = preferences
        self.palette_tokens = theme.palette(
            preferences.theme, contrast=preferences.contrast, colours=preferences.palette
        )
        self._views: dict[str, QWidget] = {}
        self._builders: dict[str, Callable[[], QWidget]] = {}
        self._buttons: dict[str, QPushButton] = {}
        self._projects: list[dict] = []
        self._analysis_refs: dict[str, str] = {}
        self._current_key = ""
        self.current_analysis_id: int | None = None

        self.setWindowTitle(branding.PRODUCT_NAME)
        self.setMinimumSize(QSize(1024, 680))
        self._build()
        self._shortcuts()

    # ------------------------------------------------------------- structure
    def _build(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._topbar())

        split = QWidget()
        body = QHBoxLayout(split)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.sidebar = self._sidebar()
        body.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.stack.setObjectName("Content")
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(self._breadcrumb())
        right_layout.addWidget(self.stack, 1)
        body.addWidget(right, 1)

        outer.addWidget(split, 1)
        self.setCentralWidget(central)

    def _topbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("Topbar")
        bar.setFixedHeight(theme.TOPBAR_H)
        layout = QHBoxLayout(bar)
        # .topbar: 16px inline padding, 12px gap.
        layout.setContentsMargins(theme.S[4], 0, theme.S[4], 0)
        layout.setSpacing(theme.S[3])

        toggle = QPushButton()
        toggle.setProperty("variant", "ghost")
        toggle.setIcon(make_icon("sidebar", self.palette_tokens.text_2, 18))
        toggle.setFixedSize(32, 32)
        toggle.setToolTip(t("a11y.toggleSidebar"))
        toggle.clicked.connect(self.toggle_sidebar)
        layout.addWidget(toggle)

        mark = QLabel()
        mark.setObjectName("BrandMark")
        mark.setFixedSize(32, 32)
        mark.setPixmap(make_icon("appmark", self.palette_tokens.accent_ink, 18).pixmap(18, 18))
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(mark)

        brand = QWidget()
        brand_layout = QVBoxLayout(brand)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(0)
        name = label(branding.PRODUCT_NAME)
        name.setObjectName("BrandName")
        tag = label(t("app.tagline"))
        tag.setObjectName("BrandTag")
        brand_layout.addWidget(name)
        brand_layout.addWidget(tag)
        layout.addWidget(brand)
        layout.addStretch(1)

        self.search = SearchField("Ctrl K", self.palette_tokens)
        self.search.setObjectName("Search")
        self.search.setPlaceholderText(t("palette.placeholder"))
        self.search.addAction(
            make_icon("search", self.palette_tokens.text_3, 16), QLineEdit.ActionPosition.LeadingPosition
        )
        self.search.setFixedWidth(360)
        # Typing here opens the palette, which is what the field stands for.
        self.search.textEdited.connect(lambda _: self.open_palette())
        layout.addWidget(self.search)
        layout.addStretch(1)

        self.project_picker = QComboBox()
        self.project_picker.setMinimumWidth(150)
        self.project_picker.currentIndexChanged.connect(self._project_chosen)
        layout.addWidget(self.project_picker)

        self.analysis_picker = QComboBox()
        self.analysis_picker.setMinimumWidth(190)
        self.analysis_picker.currentIndexChanged.connect(self._analysis_chosen)
        layout.addWidget(self.analysis_picker)

        self.start_button = QPushButton(t("analysis.start"))
        self.start_button.setProperty("variant", "primary")
        self.start_button.setIcon(make_icon("play", self.palette_tokens.accent_ink, 16))
        self.start_button.clicked.connect(self._start_analysis)
        layout.addWidget(self.start_button)

        self.theme_button = QPushButton()
        self.theme_button.setProperty("variant", "ghost")
        self.theme_button.setFixedSize(32, 32)
        self.theme_button.setToolTip(t("common.theme"))
        self.theme_button.clicked.connect(self.toggle_theme)
        layout.addWidget(self.theme_button)
        about = QPushButton()
        about.setProperty("variant", "ghost")
        about.setIcon(make_icon("info", self.palette_tokens.text_2, 18))
        about.setFixedSize(32, 32)
        about.setToolTip(t("nav.about"))
        about.clicked.connect(lambda: self.navigate("about"))
        layout.addWidget(about)

        self._sync_theme_button()
        return bar

    def _breadcrumb(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("Breadcrumb")
        bar.setFixedHeight(41)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(theme.S[6], 0, theme.S[6], 0)
        layout.setSpacing(theme.S[2])
        self.crumb = label("", role="dim")
        self.crumb.setObjectName("CrumbText")
        layout.addWidget(self.crumb)
        layout.addStretch(1)
        return bar


    def _sidebar(self) -> QWidget:
        rail = QFrame()
        rail.setObjectName("Sidebar")
        rail.setFixedWidth(theme.NAV_WIDTH)
        layout = QVBoxLayout(rail)
        # .sidebar: 12px padding with a 4px gap between entries.
        layout.setContentsMargins(theme.S[3], theme.S[3], theme.S[3], theme.S[3])
        layout.setSpacing(theme.S[1])

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for group in NAVIGATION:
            heading = label(t(group.title_key).upper())
            heading.setObjectName("NavGroup")
            # 0.07em tracking, which the stylesheet language cannot express.
            font = heading.font()
            font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 107)
            heading.setFont(font)
            layout.addWidget(heading)
            for item in group.items:
                layout.addWidget(self._nav_button(item))
        layout.addStretch(1)
        return rail

    def _nav_button(self, item: NavItem) -> QPushButton:
        widget = QPushButton(t(item.label_key))
        widget.setProperty("nav", "true")
        widget.setCheckable(True)
        widget.setCursor(Qt.CursorShape.PointingHandCursor)
        # .nav-item .ico is 16px, smaller than the default icon size.
        widget.setIcon(make_icon(item.icon, self.palette_tokens.text_2, 16))
        widget.setIconSize(QSize(16, 16))
        widget.clicked.connect(lambda _=False, key=item.key: self.navigate(key))
        self._group.addButton(widget)
        self._buttons[item.key] = widget
        return widget

    # ---------------------------------------------------------------- routing
    def register(self, key: str, builder: Callable[[], QWidget]) -> None:
        """Views are built the first time they are opened, not at startup."""
        self._builders[key] = builder

    def navigate(self, key: str) -> None:
        if key not in self._builders:
            log.debug("no view registered for %s", key)
            return
        if key not in self._views:
            try:
                view = self._builders[key]()
            except Exception:
                log.exception("could not build view %s", key)
                self.notify_error(t("common.error"))
                return
            self._views[key] = view
            self.stack.addWidget(view)
        view = self._views[key]
        changed = self.stack.currentWidget() is not view
        self.stack.setCurrentWidget(view)
        if changed:
            motion.fade_in(view)
        self._current_key = next(
            (i.label_key for g in NAVIGATION for i in g.items if i.key == key), ""
        )
        self._update_crumb()
        button = self._buttons.get(key)
        if button is not None:
            button.setChecked(True)
        refresh = getattr(view, "refresh", None)
        if callable(refresh):
            refresh()

    def toggle_sidebar(self) -> None:
        collapsed = self.sidebar.isVisible()
        if collapsed:
            motion.slide_width(self.sidebar, theme.NAV_WIDTH, 0)
        self.sidebar.setVisible(not collapsed)
        if not collapsed:
            self.sidebar.setMaximumWidth(theme.NAV_WIDTH)
            motion.slide_width(self.sidebar, 0, theme.NAV_WIDTH)
        self.prefs.sidebar_collapsed = collapsed
        prefs_store.save(self.prefs)

    def toggle_theme(self) -> None:
        """Flip between the two schemes and restyle in place."""
        from PySide6.QtWidgets import QApplication

        from app.ui.main import apply_appearance

        self.prefs.theme = "light" if self.prefs.theme == "dark" else "dark"
        prefs_store.save(self.prefs)
        app = QApplication.instance()
        if app is not None:
            apply_appearance(app, self)
        self._sync_theme_button()

    def _sync_theme_button(self) -> None:
        # The button offers the other scheme, so it shows that one's symbol -
        # a character, as the original does, since there is no moon glyph.
        self.theme_button.setText("\u263e" if self.prefs.theme == "dark" else "\u2600")

    # ------------------------------------------------------- topbar pickers
    def load_pickers(self) -> None:
        """Fill the project and analysis selectors from the workspace."""
        from app.ui import workers

        def done(envelope: object) -> None:
            try:
                projects = workers.unwrap(envelope) or []
            except Exception:  # noqa: BLE001 - the pickers are not essential
                log.debug("could not load the pickers", exc_info=True)
                return
            self._projects = list(projects)
            self.project_picker.blockSignals(True)
            self.project_picker.clear()
            for project in self._projects:
                self.project_picker.addItem(str(project.get("name", "")), project.get("id"))
            self.project_picker.blockSignals(False)
            self._project_chosen()

        workers.run(self.api.projects_list, {}, owner=self, on_done=done)

    def _project_chosen(self) -> None:
        from app.ui import workers

        project_id = self.project_picker.currentData()
        if project_id is None:
            return

        def done(envelope: object) -> None:
            try:
                runs = workers.unwrap(envelope) or []
            except Exception:  # noqa: BLE001
                return
            self.analysis_picker.blockSignals(True)
            self.analysis_picker.clear()
            for run in runs:
                if run.get("status") != "succeeded":
                    continue
                when = str(run.get("finished_at") or run.get("created_at") or "")[:19].replace("T", " ")
                ref = str(run.get("ref") or "—")
                self.analysis_picker.addItem(f"{ref} · {when}", run.get("id"))
                # The breadcrumb names the ref alone; the row carries the date.
                self.analysis_picker.setItemData(
                    self.analysis_picker.count() - 1, ref, Qt.ItemDataRole.ToolTipRole
                )
            self.analysis_picker.blockSignals(False)
            self._analysis_refs = {
                str(run.get("id")): str(run.get("ref") or "—") for run in runs
            }
            self._analysis_chosen()

        workers.run(self.api.analyses_list, {"project_id": project_id}, owner=self, on_done=done)

    def _analysis_chosen(self) -> None:
        analysis_id = self.analysis_picker.currentData()
        if analysis_id is not None:
            self.current_analysis_id = analysis_id
        self._update_crumb()
        self.refresh_current()

    def _start_analysis(self) -> None:
        from app.ui import workers

        project_id = self.project_picker.currentData()
        if project_id is None:
            self.notify_error(t("common.required"))
            return
        workers.run(
            self.api.analysis_start,
            {"project_id": project_id},
            owner=self,
            on_done=lambda _: self.navigate("analyses"),
        )

    def _update_crumb(self) -> None:
        project = self.project_picker.currentText() or "—"
        analysis = self._analysis_refs.get(str(self.analysis_picker.currentData()), "—")
        view = self._current_key or ""
        self.crumb.setText(f"{project}  /  {analysis}  /  {t(view) if view else ''}")

    # ------------------------------------------------------------- shortcuts
    def _shortcuts(self) -> None:
        # The same bindings the WebView2 build registers, so muscle memory
        # carries between the two interfaces.
        for keys, action in (
            ("Ctrl+K", self.open_palette),
            ("Ctrl+Shift+P", self.open_palette),
            ("Ctrl+Return", self._start_analysis),
            ("Ctrl+1", lambda: self.navigate("dashboard")),
            ("Ctrl+2", lambda: self.navigate("scorecard")),
            ("Ctrl+3", lambda: self.navigate("roadmap")),
            ("Ctrl+4", lambda: self.navigate("diagrams")),
            ("Ctrl+B", self.toggle_sidebar),
            ("Shift+?", self.show_shortcuts),
            ("F5", self.refresh_current),
        ):
            QShortcut(QKeySequence(keys), self, activated=action)

    def show_shortcuts(self) -> None:
        """The same list the original generates from its live bindings."""
        rows = (
            ("Ctrl+K  /  Ctrl+Shift+P", t("palette.title")),
            ("Ctrl+Enter", t("analysis.start")),
            ("Ctrl+1", t("nav.dashboard")),
            ("Ctrl+2", t("nav.scorecard")),
            ("Ctrl+3", t("nav.roadmap")),
            ("Ctrl+4", t("nav.diagrams")),
            ("Ctrl+B", t("a11y.toggleSidebar")),
            ("Shift+?", t("shortcuts.title")),
        )
        QMessageBox.information(
            self, t("shortcuts.title"), "\n".join(f"{keys}\t{what}" for keys, what in rows)
        )

    def open_palette(self) -> None:
        from app.ui.palette import CommandPalette, build_commands

        dialog = CommandPalette(self, build_commands(self))
        dialog.exec()

    def refresh_current(self) -> None:
        view = self.stack.currentWidget()
        refresh = getattr(view, "refresh", None)
        if callable(refresh):
            refresh()

    def resolve_current_analysis(self) -> None:
        """Point the quality views at the most recent successful run."""
        from app.ui import workers

        def done(envelope: object) -> None:
            try:
                projects = workers.unwrap(envelope) or []
            except Exception:  # noqa: BLE001 - startup convenience only
                log.debug("could not resolve the current analysis", exc_info=True)
                return
            for project in projects:
                latest = project.get("latest_analysis") or {}
                if latest.get("status") == "succeeded":
                    self.current_analysis_id = latest.get("id")
                    return

        workers.run(self.api.projects_list, {}, owner=self, on_done=done)

    # -------------------------------------------------------------- feedback
    def notify_error(self, message: str) -> None:
        QMessageBox.warning(self, t("common.error"), message)

    def notify(self, message: str) -> None:
        QMessageBox.information(self, branding.PRODUCT_NAME, message)

    # ------------------------------------------------- file dialogs for the API
    def pick_folder(self) -> str | None:
        return QFileDialog.getExistingDirectory(self, t("project.browse")) or None

    def save_file(self, filename: str) -> str | None:
        path, _ = QFileDialog.getSaveFileName(self, t("common.save"), filename)
        return path or None
