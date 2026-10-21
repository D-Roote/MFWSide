"""Explicit storage inspection/cleanup; workers never access GUI objects."""
from PySide6.QtCore import QEvent, QSize, QThread, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
    QStyle, QStyleOptionViewItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from app.storageManagement import cleanup_storage, format_size, scan_storage, storage_groups
from app.dialogs import confirm_deletion


class StorageUsageTree(QTreeWidget):
    """Fit whole rows up to the former height limit; keep the header fixed."""

    HEIGHT_LIMIT = 300

    def __init__(self, parent=None):
        super().__init__(parent)
        self._height_timer = QTimer(self)
        self._height_timer.setSingleShot(True)
        self._height_timer.timeout.connect(self._fit_height)
        for signal in (self.model().rowsInserted, self.model().rowsRemoved,
                       self.model().modelReset, self.model().dataChanged):
            signal.connect(self._queue_height)
        self.header_scroll_corner = QFrame()
        self.header_scroll_corner.setObjectName("storageHeaderScrollCorner")
        self.addScrollBarWidget(self.header_scroll_corner, Qt.AlignmentFlag.AlignTop)
        self.header_scroll_corner.setFixedHeight(self.header().height())
        self.header().installEventFilter(self)
        self._queue_height()

    def _queue_height(self, *_args):
        self._height_timer.start(0)

    def _fit_height(self):
        self.ensurePolished()
        self.doItemsLayout()
        rows = self.topLevelItemCount()
        if rows:
            row_height = self.sizeHintForRow(0)
        else:
            # Measure a blank checkbox row with the active QSS/font instead of
            # inserting a fake selectable item or hard-coding its pixel size.
            option = QStyleOptionViewItem()
            self.initViewItemOption(option)
            option.features |= (QStyleOptionViewItem.ViewItemFeature.HasDisplay
                                | QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator)
            option.text = " "
            row_height = self.style().sizeFromContents(
                QStyle.ContentsType.CT_ItemViewItem, option,
                QSize(0, self.fontMetrics().height()), self).height()
        height = min(self.HEIGHT_LIMIT, self.header().height() + 2 * self.frameWidth()
                     + max(1, rows) * max(1, row_height))
        if self.minimumHeight() != height or self.maximumHeight() != height:
            self.setFixedHeight(height)

    def event(self, event):
        handled = super().event(event)
        if (event.type() in (QEvent.Type.Show, QEvent.Type.FontChange, QEvent.Type.StyleChange)
                and hasattr(self, "_height_timer")):
            self._queue_height()
        return handled

    def eventFilter(self, watched, event):
        if watched is self.header() and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.header_scroll_corner.setFixedHeight(self.header().height())
            self._queue_height()
        return super().eventFilter(watched, event)


class StorageUsageItem(QTreeWidgetItem):
    """Sort raw sizes/counts, and keep deletion targets attached to their rows."""

    def __init__(self, usage):
        super().__init__([usage.group.label, format_size(usage.bytes),
                          format_size(usage.removable_bytes), str(usage.files)])
        self.usage = usage
        self.setCheckState(0, Qt.CheckState.Unchecked)
        self.setToolTip(0, str(usage.group.root) + ("\n" + "\n".join(usage.errors) if usage.errors else ""))
        for column in (1, 2, 3):
            self.setTextAlignment(column, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def __lt__(self, other):
        if not isinstance(other, StorageUsageItem):
            return super().__lt__(other)
        tree = self.treeWidget()
        column = tree.sortColumn() if tree is not None else 0
        if column in (1, 2, 3):
            attribute = ("bytes", "removable_bytes", "files")[column - 1]
            return (getattr(self.usage, attribute), self.usage.group.key) < (
                getattr(other.usage, attribute), other.usage.group.key)
        return (self.text(0).casefold(), self.usage.group.key) < (
            other.text(0).casefold(), other.usage.group.key)


class StorageWorker(QThread):
    def __init__(self, user_dir, groups, usages=None, parent=None):
        super().__init__(parent)
        self.user_dir, self.groups, self.usages = user_dir, groups, usages
        self.result = None
        self.error = ""

    def run(self):
        try:
            if self.usages is None:
                self.result = scan_storage(self.groups, self.user_dir, self.isInterruptionRequested)
            else:
                self.result = cleanup_storage(self.usages, self.user_dir, self.isInterruptionRequested)
        except Exception as error:
            self.error = str(error)


class ManagementPanel(QWidget):
    reset_requested = Signal()
    busy_changed = Signal(bool)
    shutdown_ready = Signal()
    COLUMN_LABELS = ("항목", "사용 용량", "정리 가능", "파일 수")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None
        self.closing = False
        self.allowed = True
        self.guard = lambda: True
        self.usages = []
        self.paths = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 12)
        layout.setSpacing(12)
        self.tree = StorageUsageTree()
        self.tree.setObjectName("storageUsageTree")
        self.tree.setAccessibleName("폴더별 로그 용량 및 삭제 대상")
        self.tree.setHeaderLabels(self.COLUMN_LABELS)
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tree.setSortingEnabled(True)
        self.tree.sortItems(2, Qt.SortOrder.DescendingOrder)
        self.tree.header().setSectionsClickable(True)
        self.tree.header().setHighlightSections(False)
        self.tree.header().setSortIndicatorShown(False)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            self.tree.header().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.tree)
        buttons = QHBoxLayout()
        self.select_all_button = QPushButton("모두 선택")
        self.scan_button = QPushButton("용량 조회")
        self.delete_button = QPushButton("선택한 로그 삭제")
        for button in (self.select_all_button, self.scan_button, self.delete_button):
            button.setObjectName("monitorActionButton")
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.status = QLabel()
        self.status.setObjectName("settingsRowDescription")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setAccessibleName("관리 결과")
        layout.addWidget(self.status)
        self.status.hide()
        self.reset_row = QFrame()
        self.reset_row.setObjectName("settingsRow")
        reset_layout = QGridLayout(self.reset_row)
        reset_layout.setContentsMargins(0, 12, 0, 0)
        reset_layout.setHorizontalSpacing(16)
        reset_layout.setVerticalSpacing(2)
        reset_layout.setColumnStretch(0, 1)
        reset_title = QLabel("모든 설정 초기화")
        reset_title.setObjectName("settingsRowTitle")
        reset_description = QLabel("작업·옵션·연결·창 배치 등 모든 사용자 설정을 기본값으로 돌립니다. 초기화 후 앱이 종료됩니다.")
        reset_description.setObjectName("settingsRowDescription")
        reset_description.setWordWrap(True)
        reset_layout.addWidget(reset_title, 0, 0)
        reset_layout.addWidget(reset_description, 1, 0)
        self.reset_button = QPushButton("초기화")
        self.reset_button.setObjectName("monitorActionButton")
        self.reset_button.setAccessibleName("모든 사용자 설정 초기화")
        reset_layout.addWidget(self.reset_button, 0, 1, 2, 1, Qt.AlignmentFlag.AlignVCenter)
        self.reset_row.title_label = reset_title
        self.reset_row.description_label = reset_description
        layout.addWidget(self.reset_row)
        self.select_all_button.clicked.connect(self._toggle_select_all)
        self.scan_button.clicked.connect(self.scan)
        self.delete_button.clicked.connect(self.delete_selected)
        self.reset_button.clicked.connect(self.reset_requested)
        self.tree.itemChanged.connect(self._refresh_buttons)
        self._refresh_buttons()

    @property
    def busy(self):
        return self.worker is not None

    def configure(self, user_dir, root_dir, app_dir, resource_dir, guard):
        self.paths = (user_dir, root_dir, app_dir, resource_dir)
        self.guard = guard

    def set_allowed(self, allowed):
        self.allowed = bool(allowed)
        self._refresh_buttons()

    def set_status(self, message):
        self.status.setText(message)
        self.status.setVisible(bool(message))

    def _selected(self):
        return [self.tree.topLevelItem(row).usage for row in range(self.tree.topLevelItemCount())
                if self.tree.topLevelItem(row).checkState(0) == Qt.CheckState.Checked]

    def _toggle_select_all(self):
        if not self.allowed or self.busy or self.closing:
            return
        count = self.tree.topLevelItemCount()
        state = Qt.CheckState.Unchecked if count and len(self._selected()) == count else Qt.CheckState.Checked
        was_blocked = self.tree.blockSignals(True)
        try:
            for row in range(count):
                self.tree.topLevelItem(row).setCheckState(0, state)
        finally:
            self.tree.blockSignals(was_blocked)
        self._refresh_buttons()

    def _refresh_buttons(self, *_args):
        enabled = self.allowed and not self.busy and not self.closing
        self.scan_button.setEnabled(not self.busy and not self.closing and self.paths is not None)
        selected = self._selected()
        count = self.tree.topLevelItemCount()
        self.select_all_button.setEnabled(enabled and count > 0)
        self.select_all_button.setText("모두 해제" if count and len(selected) == count else "모두 선택")
        self.delete_button.setEnabled(enabled and bool(selected))
        self.reset_button.setEnabled(enabled)

    def scan(self):
        if self.busy or self.closing or self.paths is None:
            return
        self._start(StorageWorker(self.paths[0], storage_groups(*self.paths), parent=self))

    def delete_selected(self):
        if self.busy or self.closing or not self.allowed or not self.guard():
            self.set_status("작업·화면 모니터링·실행 테스트가 끝난 뒤 삭제하세요.")
            return
        selected = self._selected()
        if not selected:
            return
        folders = "\n".join(usage.group.label for usage in selected)
        size = format_size(sum(usage.removable_bytes for usage in selected))
        if confirm_deletion(self, "로그 영구 삭제",
                            f"다음 폴더의 로그·오류 이미지·캐시를 영구 삭제합니다.\n정리 가능 용량: {size}\n\n{folders}\n\n"
                            "휴지통으로 이동하지 않으며 복구할 수 없습니다.\n\n계속하시겠습니까?",
                            right_padding=24) != QMessageBox.StandardButton.Yes:
            return
        if not self.guard() or self.closing:
            self.set_status("상태가 변경되어 삭제를 취소했습니다.")
            return
        self._start(StorageWorker(self.paths[0], [], selected, self))

    def _start(self, worker):
        self.worker = worker
        worker.finished.connect(self._finished)
        self._refresh_buttons()
        self.set_status("로그를 정리하고 있습니다…" if worker.usages is not None else "폴더 용량을 조회하고 있습니다…")
        self.busy_changed.emit(True)
        try:
            worker.start()
        except RuntimeError as error:
            self.worker = None
            worker.deleteLater()
            self.set_status(f"관리 작업을 시작하지 못했습니다: {error}")
            self.busy_changed.emit(False)
            self._refresh_buttons()

    @Slot()
    def _finished(self):
        worker, self.worker = self.worker, None
        if worker is None:
            return
        worker.deleteLater()
        if worker.error:
            self.set_status("관리 작업 실패: " + worker.error)
        elif worker.usages is None and worker.result is not None:
            # Zero-byte/protected-only folders are not cleanup candidates. Keep
            # the complete scan for totals and access-error reporting below.
            self.usages = [usage for usage in worker.result if usage.removable_bytes > 0]
            sort_column = self.tree.sortColumn()
            sort_order = self.tree.header().sortIndicatorOrder()
            self.tree.blockSignals(True)
            self.tree.setSortingEnabled(False)
            self.tree.clear()
            for usage in self.usages:
                self.tree.addTopLevelItem(StorageUsageItem(usage))
            self.tree.setSortingEnabled(True)
            self.tree.header().setSortIndicatorShown(False)
            self.tree.sortItems(sort_column, sort_order)
            self.tree.blockSignals(False)
            total = format_size(sum(usage.bytes for usage in worker.result))
            removable = format_size(sum(usage.removable_bytes for usage in worker.result))
            errors = sum(bool(usage.errors) for usage in worker.result)
            self.set_status(("정리 가능한 폴더가 없습니다. " if not self.usages else "")
                                + f"사용 용량 {total}, 정리 가능 {removable}. 사용 중인 파일·설정·리소스는 제외합니다."
                                + (f" {errors}개 폴더는 접근 제한이 있어 일부 조회되지 않았습니다." if errors else ""))
        elif worker.result is not None:
            result = worker.result
            self.set_status(f"{result.files}개 파일, {format_size(result.bytes)} 삭제 완료. 보호·갱신 파일 {result.skipped}개 제외."
                                + ("\n삭제하지 못한 항목: " + "\n".join(result.errors) if result.errors else ""))
            self.usages = []
            self.tree.clear()
        self._refresh_buttons()
        self.busy_changed.emit(False)
        if self.closing:
            self.shutdown_ready.emit()

    def shutdown(self):
        self.closing = True
        if self.worker is not None:
            self.worker.requestInterruption()
        self._refresh_buttons()
        return not self.busy
