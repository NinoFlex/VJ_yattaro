"""Show Shazam history as title / artist / comment, with visible vote status.

Timestamp tuples remain internal for confirmation and backwards compatibility.
Transient state is rendered in the comment column, never in searchable or saved
comment metadata. The original timestamp is still available in tooltips.
"""
from PySide6.QtCore import Qt
from ui.widgets.right_table_view import RightTableModel


class ShazamHistoryModel(RightTableModel):
    # MainWindow uses these exact suffixes on the timestamp of its preview row.
    # Do not derive status from the title or the actual tie-up comment.
    _PREVIEW_LABELS = ("(\u4eee)", "(\u78ba\u5b9a\u30fb\u60c5\u5831\u88dc\u5b8c\u4e2d)")

    def __init__(self, data=None, comment_provider=None):
        super().__init__(
            data,
            headers=["\u66f2\u540d", "\u30a2\u30fc\u30c6\u30a3\u30b9\u30c8\u540d", "\u30b3\u30e1\u30f3\u30c8"],
            max_rows=50,
        )
        self.comment_provider = comment_provider or (lambda title, artist: "")

    def comment_for_row(self, row):
        """Return only real comment text, including for manual YouTube searches."""
        if not 0 <= row < len(self._data) or len(self._data[row]) < 3:
            return ""
        entry = self._data[row]
        value = self.comment_provider(entry[1], entry[2])
        return str(value or "")

    def _display_comment_for_row(self, row):
        """Decorate the displayed cell without modifying the stored comment."""
        comment = self.comment_for_row(row)
        timestamp = str(self._data[row][0] or "").rstrip()
        for label in self._PREVIEW_LABELS:
            if timestamp.endswith(" " + label):
                return label + (" " + comment if comment else "")
        return comment

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._data):
            return None
        row = self._data[index.row()]
        if len(row) < 3:
            return ""
        if role == Qt.DisplayRole:
            if index.column() == 2:
                return self._display_comment_for_row(index.row())
            return row[index.column() + 1] if 0 <= index.column() < 2 else ""
        if role == Qt.ToolTipRole:
            return f"{row[0]}\n{row[1]}\n{row[2]}\n{self.comment_for_row(index.row())}".strip()
        return None

    def refresh_comments(self):
        if self.rowCount():
            self.dataChanged.emit(
                self.index(0, 2), self.index(self.rowCount() - 1, 2),
                [Qt.DisplayRole, Qt.ToolTipRole],
            )
