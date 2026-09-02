import math
import time

from PySide6.QtCore import Qt, QRectF, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)



def _finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_duration_text(value):
    if not value:
        return None
    parts = str(value).strip().split(":")
    if not parts or any(not part.isdigit() for part in parts):
        return None
    try:
        total = 0
        for part in parts:
            total = total * 60 + int(part)
        return float(total) if total > 0 else None
    except (TypeError, ValueError):
        return None


def _format_time(seconds, unknown="--:--"):
    value = _finite_number(seconds)
    if value is None or value < 0:
        return unknown
    value = int(value)
    hours, rem = divmod(value, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class SpinnerPlaybackButton(QAbstractButton):
    """Circular play/pause indicator with a rotating hand while playing."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFixedSize(44, 44)
        self.setToolTip("Play / pause")
        self._spinning = False
        self._theme = "dark"
        self._angle = 0.0

        # Draw the animation ourselves rather than depending on an animated GIF.
        # This keeps the indicator moving reliably in PyInstaller builds too.
        self._animation_timer = QTimer(self)
        self._animation_timer.setTimerType(Qt.PreciseTimer)
        self._animation_timer.setInterval(33)  # about 30 fps
        self._animation_timer.timeout.connect(self._advance_animation)

    def is_spinning(self):
        return self._spinning

    def set_spinning(self, enabled):
        enabled = bool(enabled)
        if enabled == self._spinning:
            return
        self._spinning = enabled
        if enabled:
            if not self._animation_timer.isActive():
                self._animation_timer.start()
        else:
            self._animation_timer.stop()
        self.update()

    def _advance_animation(self):
        # One revolution in roughly 1.2 seconds.  Keep the current angle when
        # paused so the hand visibly freezes where playback stopped.
        self._angle = (self._angle + 10.0) % 360.0
        self.update()

    def apply_theme(self, theme):
        self._theme = theme
        self.update()

    def paintEvent(self, event):
        del event
        from ui.theme import colors

        c = colors(self._theme)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        # Outer ring.
        ring = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        painter.setPen(QPen(QColor(c["accent"]), 3))
        painter.setBrush(QColor(c["panel_alt"]))
        painter.drawEllipse(ring)

        center = ring.center()
        radius = ring.width() / 2.0

        # Rotating hand: angle 0 points straight up.
        radians = math.radians(self._angle - 90.0)
        inner = radius * 0.10
        outer = radius * 0.72
        x1 = center.x() + math.cos(radians) * inner
        y1 = center.y() + math.sin(radians) * inner
        x2 = center.x() + math.cos(radians) * outer
        y2 = center.y() + math.sin(radians) * outer

        hand_color = QColor(c["text"])
        painter.setPen(QPen(hand_color, 5, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(int(x1), int(y1), int(x2), int(y2))

        # Small hub makes the direction of the rotating hand easier to read.
        painter.setPen(Qt.NoPen)
        painter.setBrush(hand_color)
        painter.drawEllipse(center, 3.0, 3.0)




class PlayerTargetToggle(QAbstractButton):
    """A/B target selector. Unchecked selects A and checked selects B."""

    target_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFixedSize(100, 30)
        self._theme = "dark"
        self.toggled.connect(self._emit_target)
        self._emit_target(False)

    def selected_player(self):
        return "B" if self.isChecked() else "A"

    def set_selected_player(self, player_id, notify=True):
        checked = str(player_id).upper() == "B"
        if notify:
            self.setChecked(checked)
            return

        # Synchronize the visual toggle to browser feedback without sending a
        # SELECT_PLAYER command back to the browser.
        previous = self.blockSignals(True)
        try:
            self.setChecked(checked)
        finally:
            self.blockSignals(previous)
        selected = "B" if checked else "A"
        self.setToolTip(f"Control target: Player {selected}")
        self.update()

    def _emit_target(self, checked):
        player_id = "B" if checked else "A"
        self.setToolTip(f"Control target: Player {player_id}")
        self.target_changed.emit(player_id)
        self.update()

    def apply_theme(self, theme):
        self._theme = theme
        self.update()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.toggle()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        del event
        from ui.theme import colors

        c = colors(self._theme)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = rect.height() / 2
        painter.setPen(QPen(QColor(c["border"]), 1))
        painter.setBrush(QColor(c["input"]))
        painter.drawRoundedRect(rect, radius, radius)

        margin = 4
        thumb_size = rect.height() - margin * 2
        selected_b = self.isChecked()
        thumb_x = rect.right() - margin - thumb_size if selected_b else rect.left() + margin
        thumb_rect = QRectF(thumb_x, rect.top() + margin, thumb_size, thumb_size)
        painter.setPen(QPen(QColor(c["accent"]), 1))
        painter.setBrush(QColor(c["accent"]))
        painter.drawEllipse(thumb_rect)

        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        # Keep the A/B labels farther apart so the selected side reads at a glance.
        left_rect = QRectF(rect.left() + 5, rect.top(), 38, rect.height())
        right_rect = QRectF(rect.right() - 43, rect.top(), 38, rect.height())
        painter.setPen(QColor(c["accent_text"] if not selected_b else c["text"]))
        painter.drawText(left_rect, Qt.AlignCenter, "A")
        painter.setPen(QColor(c["accent_text"] if selected_b else c["text"]))
        painter.drawText(right_rect, Qt.AlignCenter, "B")


class PlayerControlPanel(QFrame):
    """Compact status and play/pause panel for one browser player."""

    playback_clicked = Signal(str)

    def __init__(self, player_id, parent=None):
        super().__init__(parent)
        self.player_id = str(player_id).upper()
        self.video_id = ""
        self.state = "idle"
        self._theme = "dark"
        self._track_info = {}
        self._media_info = {}
        self._duration = 0.0
        self._anchor_position = 0.0
        self._anchor_monotonic = time.monotonic()
        self._active_output = False
        self._thumbnail_url = ""
        self._network = QNetworkAccessManager(self)
        self._network.finished.connect(self._on_thumbnail_finished)

        self.setObjectName(f"player_control_panel_{self.player_id}")
        self.setFixedHeight(56)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        root = QHBoxLayout(self)
        root.setContentsMargins(3, 2, 4, 2)
        root.setSpacing(5)

        self.spinner_button = SpinnerPlaybackButton(self)
        self.spinner_button.clicked.connect(
            lambda _checked=False: self.playback_clicked.emit(self.player_id)
        )
        root.addWidget(self.spinner_button, 0, Qt.AlignVCenter)

        self.thumbnail_label = QLabel(self.player_id)
        self.thumbnail_label.setObjectName("player_thumbnail")
        self.thumbnail_label.setAlignment(Qt.AlignCenter)
        self.thumbnail_label.setFixedSize(82, 46)
        self.thumbnail_label.setScaledContents(False)
        root.addWidget(self.thumbnail_label, 0, Qt.AlignVCenter)

        info_column = QVBoxLayout()
        info_column.setContentsMargins(0, 0, 0, 0)
        info_column.setSpacing(0)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(4)
        self.info_label = QLabel(f"Player {self.player_id} - waiting")
        self.info_label.setObjectName("player_info")
        self.info_label.setTextFormat(Qt.PlainText)
        self.info_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.info_label.setMinimumWidth(0)
        title_row.addWidget(self.info_label, 1)

        self.time_label = QLabel("00:00 / --:--")
        self.time_label.setObjectName("player_time")
        self.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.time_label.setMinimumWidth(88)
        title_row.addWidget(self.time_label, 0)
        info_column.addLayout(title_row)

        self.state_label = QLabel("WAITING")
        self.state_label.setObjectName("player_state")
        self.state_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        # Reuse the compact second line for state without increasing panel height.
        self.state_label.setFixedHeight(11)
        info_column.addWidget(self.state_label)

        self.progress = QProgressBar()
        self.progress.setObjectName("player_progress")
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        self.progress.setFocusPolicy(Qt.NoFocus)
        info_column.addWidget(self.progress)
        root.addLayout(info_column, 1)

        self.apply_theme(self._theme)

    @property
    def duration(self):
        return self._duration

    @property
    def current_position(self):
        return self.estimated_position()

    def is_playing(self):
        return self.state == "playing"

    def set_active_output(self, active):
        active = bool(active)
        if active != self._active_output:
            self._active_output = active
            self.setProperty("activeOutput", active)
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()

    def set_metadata(self, video_id=None, track_info=None, media_info=None):
        video_changed = False
        if video_id is not None:
            new_video_id = str(video_id or "")
            video_changed = new_video_id != self.video_id
            if video_changed:
                self.video_id = new_video_id
                self._duration = 0.0
                self._anchor_position = 0.0
                self._anchor_monotonic = time.monotonic()
                self._track_info = {}
                self._media_info = {}
                self._thumbnail_url = ""
                self.thumbnail_label.setPixmap(QPixmap())
                self.thumbnail_label.setText(self.player_id)
        if isinstance(track_info, dict):
            self._track_info = dict(track_info)
        if isinstance(media_info, dict):
            self._media_info = dict(media_info)

        duration_hint = _parse_duration_text(self._media_info.get("durationText"))
        if duration_hint and self._duration <= 0:
            self._duration = duration_hint

        thumbnail_url = str(self._media_info.get("thumbnailUrl", "") or "")
        if not thumbnail_url and self.video_id:
            thumbnail_url = f"https://i.ytimg.com/vi/{self.video_id}/hqdefault.jpg"
        if thumbnail_url and thumbnail_url != self._thumbnail_url:
            self._load_thumbnail(thumbnail_url)

        # The controller shows the title reported by the actual YouTube player.
        # Track metadata is only a fallback while the IFrame API has not exposed
        # the video's own title yet.
        video_title = str(self._media_info.get("videoTitle", "") or "").strip()
        if video_title:
            text = video_title
        else:
            parts = []
            for key in ("title", "artist", "comment"):
                value = str(self._track_info.get(key, "") or "").strip()
                if value:
                    parts.append(value)
            text = "  -  ".join(parts) if parts else f"Player {self.player_id} - waiting"
        self.info_label.setText(text)
        self.info_label.setToolTip(text)
        self._refresh_display()
        return video_changed

    def set_state(
        self,
        state,
        current_time=None,
        duration=None,
        video_id=None,
        track_info=None,
        media_info=None,
        is_current=None,
    ):
        now = time.monotonic()
        previous_position = self.estimated_position(now)
        video_changed = False
        if video_id is not None or track_info is not None or media_info is not None:
            video_changed = self.set_metadata(video_id, track_info, media_info)

        position_value = _finite_number(current_time)
        duration_value = _finite_number(duration)
        if duration_value is not None and duration_value > 0:
            self._duration = duration_value
        if position_value is None:
            position_value = 0.0 if video_changed else previous_position
        self._anchor_position = max(0.0, position_value)
        if self._duration > 0:
            self._anchor_position = min(self._anchor_position, self._duration)
        self._anchor_monotonic = now
        self.state = str(state or "idle").lower()
        if is_current is not None:
            self.set_active_output(bool(is_current))

        # Keep the play indicator independent from transient player states.
        # Only explicit PLAYING / PAUSED feedback is allowed to start or stop
        # the rotation.  States such as buffering, ready, preloading, cued,
        # heartbeat, etc. must not interrupt an animation that is already
        # running (or restart one that is paused).
        if self.state == "playing":
            self.spinner_button.set_spinning(True)
        elif self.state in ("paused", "pause"):
            self.spinner_button.set_spinning(False)

        self._refresh_display(now)

    def adjust_position(self, delta_seconds):
        now = time.monotonic()
        new_position = self.estimated_position(now) + float(delta_seconds)
        new_position = max(0.0, new_position)
        if self._duration > 0:
            new_position = min(new_position, self._duration)
        self._anchor_position = new_position
        self._anchor_monotonic = now
        self._refresh_display(now)

    def estimated_position(self, now=None):
        now = now if now is not None else time.monotonic()
        position = self._anchor_position
        if self.state == "playing":
            position += max(0.0, now - self._anchor_monotonic)
        if self._duration > 0:
            position = min(position, self._duration)
        return max(0.0, position)

    def tick(self, now=None):
        self._refresh_display(now if now is not None else time.monotonic())

    def apply_theme(self, theme):
        from ui.theme import colors, normalize_theme

        self._theme = normalize_theme(theme)
        c = colors(self._theme)
        self.setStyleSheet(f"""
            QFrame#{self.objectName()} {{
                background-color: {c['panel_alt']};
                border: 1px solid {c['border']};
                border-radius: 5px;
            }}
            QFrame#{self.objectName()}[activeOutput="true"] {{
                border: 2px solid {c['accent']};
            }}
            QLabel#player_thumbnail {{
                background-color: {c['no_image']};
                color: {c['no_image_text']};
                border: 1px solid {c['border_soft']};
                border-radius: 3px;
                font-size: 18px;
                font-weight: bold;
            }}
            QLabel#player_info {{
                color: {c['text']};
                font-size: 12px;
                font-weight: bold;
                border: none;
                background: transparent;
            }}
            QLabel#player_time {{
                color: {c['text']};
                font-family: Consolas, monospace;
                font-size: 10px;
                border: none;
                background: transparent;
            }}
            QLabel#player_state {{
                color: {c['muted']};
                font-size: 9px;
                border: none;
                background: transparent;
            }}
            QProgressBar#player_progress {{
                background-color: {c['input']};
                border: 1px solid {c['border_soft']};
                border-radius: 3px;
            }}
            QProgressBar#player_progress::chunk {{
                background-color: {c['accent']};
                border-radius: 2px;
            }}
        """)
        self.spinner_button.apply_theme(self._theme)

    def _refresh_display(self, now=None):
        position = self.estimated_position(now)
        duration_text = _format_time(self._duration) if self._duration > 0 else "--:--"
        self.time_label.setText(f"{_format_time(position)} / {duration_text}")
        if self._duration > 0:
            value = int(max(0.0, min(1.0, position / self._duration)) * 1000)
        else:
            value = 0
        self.progress.setValue(value)
        state_names = {
            "idle": "WAITING",
            "unstarted": "WAITING",
            "preloading": "PRELOADING",
            "ready": "READY",
            "playing": "PLAYING",
            "paused": "PAUSED",
            "buffering": "BUFFERING",
            "ended": "ENDED",
            "error": "ERROR",
            "control_unavailable": "CONTROL UNAVAILABLE",
        }
        self.state_label.setText(state_names.get(self.state, self.state.upper()))
        self.spinner_button.setToolTip(
            f"Player {self.player_id}: "
            + ("pause" if self.state == "playing" else "resume")
        )

    def _load_thumbnail(self, url):
        self._thumbnail_url = url
        request = QNetworkRequest(QUrl(url))
        reply = self._network.get(request)
        reply.setProperty("thumbnailUrl", url)

    def _on_thumbnail_finished(self, reply):
        try:
            url = str(reply.property("thumbnailUrl") or "")
            if url != self._thumbnail_url:
                return
            if reply.error() != QNetworkReply.NetworkError.NoError:
                return
            pixmap = QPixmap()
            if not pixmap.loadFromData(bytes(reply.readAll())):
                return
            scaled = pixmap.scaled(
                self.thumbnail_label.size(),
                Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation,
            )
            x = max(0, (scaled.width() - self.thumbnail_label.width()) // 2)
            y = max(0, (scaled.height() - self.thumbnail_label.height()) // 2)
            cropped = scaled.copy(
                x,
                y,
                self.thumbnail_label.width(),
                self.thumbnail_label.height(),
            )
            self.thumbnail_label.setPixmap(cropped)
            self.thumbnail_label.setText("")
        finally:
            reply.deleteLater()


class DualPlayerControlBar(QWidget):
    """A/B status panels plus target toggle and seek buttons."""

    playback_requested = Signal(str)
    rewind_requested = Signal()
    forward_requested = Signal()
    target_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._theme = "dark"
        self.setObjectName("dual_player_control_bar")
        self.setFixedHeight(62)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 2, 3, 2)
        layout.setSpacing(4)

        self.panel_a = PlayerControlPanel("A", self)
        self.panel_b = PlayerControlPanel("B", self)
        self.panel_a.playback_clicked.connect(self.playback_requested)
        self.panel_b.playback_clicked.connect(self.playback_requested)
        layout.addWidget(self.panel_a, 1)

        self.center_frame = QFrame(self)
        self.center_frame.setObjectName("player_control_center")
        self.center_frame.setFixedSize(194, 56)
        controls = QHBoxLayout(self.center_frame)
        controls.setContentsMargins(5, 3, 5, 3)
        controls.setSpacing(4)

        # Kept as a hidden accessibility/status label; the A/B letters on the
        # toggle itself make a second visible row unnecessary.
        self.target_label = QLabel("Control target: A", self.center_frame)
        self.target_label.setObjectName("player_target_label")
        self.target_label.hide()

        self.rewind_button = QPushButton("\u25c0\u25c0")
        self.rewind_button.setObjectName("panel_seek_button")
        self.rewind_button.setToolTip("Rewind selected player")
        self.rewind_button.setFixedSize(36, 30)
        self.rewind_button.clicked.connect(
            lambda _checked=False: self.rewind_requested.emit()
        )
        controls.addWidget(self.rewind_button)

        self.target_toggle = PlayerTargetToggle(self.center_frame)
        self.target_toggle.target_changed.connect(self._on_target_changed)
        controls.addWidget(self.target_toggle)

        self.forward_button = QPushButton("\u25b6\u25b6")
        self.forward_button.setObjectName("panel_seek_button")
        self.forward_button.setToolTip("Forward selected player")
        self.forward_button.setFixedSize(36, 30)
        self.forward_button.clicked.connect(
            lambda _checked=False: self.forward_requested.emit()
        )
        controls.addWidget(self.forward_button)
        layout.addWidget(self.center_frame, 0)
        layout.addWidget(self.panel_b, 1)
        self.apply_theme(self._theme)

    def selected_player(self):
        return self.target_toggle.selected_player()

    def set_selected_player(self, player_id, notify=True):
        self.target_toggle.set_selected_player(player_id, notify=notify)

    def panel(self, player_id):
        return self.panel_b if str(player_id).upper() == "B" else self.panel_a

    def panels(self):
        return (self.panel_a, self.panel_b)

    def interactive_widgets(self):
        return (
            self.panel_a.spinner_button,
            self.panel_b.spinner_button,
            self.rewind_button,
            self.forward_button,
            self.target_toggle,
        )

    def tick(self):
        now = time.monotonic()
        self.panel_a.tick(now)
        self.panel_b.tick(now)

    def apply_theme(self, theme):
        from ui.theme import colors, normalize_theme

        self._theme = normalize_theme(theme)
        c = colors(self._theme)
        self.setStyleSheet(f"""
            QWidget#dual_player_control_bar {{
                background-color: {c['panel']};
                border-bottom: 1px solid {c['border_soft']};
            }}
            QFrame#player_control_center {{
                background-color: {c['panel_alt']};
                border: 1px solid {c['border']};
                border-radius: 5px;
            }}
            QLabel#player_target_label {{
                color: {c['muted']};
                font-size: 10px;
                font-weight: bold;
                border: none;
                background: transparent;
            }}
            QPushButton#panel_seek_button {{
                background-color: {c['input']};
                color: {c['text']};
                border: 1px solid {c['border']};
                border-radius: 4px;
                font-size: 13px;
                font-weight: bold;
                padding: 0;
            }}
            QPushButton#panel_seek_button:hover {{
                background-color: {c['hover']};
            }}
            QPushButton#panel_seek_button:pressed {{
                background-color: {c['selection_soft']};
            }}
        """)
        self.panel_a.apply_theme(self._theme)
        self.panel_b.apply_theme(self._theme)
        self.target_toggle.apply_theme(self._theme)

    def _on_target_changed(self, player_id):
        self.target_label.setText(f"Control target: {player_id}")
        self.target_changed.emit(player_id)
