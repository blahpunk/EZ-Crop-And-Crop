#!/usr/bin/env python3

import sys
import os
import subprocess

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QSlider, QPushButton,
    QHBoxLayout, QVBoxLayout, QFileDialog, QLineEdit, QComboBox, QMessageBox, QSizePolicy
)
from PyQt5.QtCore import Qt, QRect, QTimer, QPoint, QSize, QSettings
from PyQt5.QtGui import QPixmap, QImage, QPainter, QPen, QColor

import cv2  # pip install opencv-python-headless

ASPECT_PRESETS = {
    "Manual": None,
    "Square (1:1)": 1.0,
    "Portrait (4:5)": 4 / 5,
    "Landscape (1.91:1)": 1.91,
    "Story / Reel (9:16)": 9 / 16,
    "Carousel Portrait (4:5)": 4 / 5,
    "Carousel Square (1:1)": 1.0,
    "Carousel Landscape (1.91:1)": 1.91,
}


class CropOverlay(QWidget):
    def __init__(self, parent, video_size, aspect_ratio=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowFlags(Qt.Widget | Qt.FramelessWindowHint)
        self.video_size = video_size
        self.aspect_ratio = aspect_ratio
        self.setMouseTracking(True)
        self.dragging = False
        self.resizing = False
        self.drag_edge = None
        self.drag_offset = QPoint()
        self.min_size = 32
        self.crop = [0, 0, video_size[0], video_size[1]]
        self.setCursor(Qt.CrossCursor)
        self.ratio_w = 1.0
        self.ratio_h = 1.0
        self.update_crop_rect()
        self.edge_margin = 10
        self.corner_size = 15

    def set_aspect_ratio(self, aspect):
        self.aspect_ratio = aspect
        self.snap_to_aspect()

    def set_crop(self, crop):
        x, y, w, h = crop
        x = max(0, min(x, self.video_size[0] - self.min_size))
        y = max(0, min(y, self.video_size[1] - self.min_size))
        w = max(self.min_size, min(w, self.video_size[0] - x))
        h = max(self.min_size, min(h, self.video_size[1] - y))

        if self.aspect_ratio:
            if w / h > self.aspect_ratio:
                w = int(h * self.aspect_ratio)
            else:
                h = int(w / self.aspect_ratio)

        self.crop = [x, y, w, h]
        self.update_crop_rect()
        self.update()

    def snap_to_aspect(self):
        if not self.aspect_ratio:
            return
        x, y, w, h = self.crop
        center_x = x + w // 2
        center_y = y + h // 2
        if w / h > self.aspect_ratio:
            w = int(h * self.aspect_ratio)
        else:
            h = int(w / self.aspect_ratio)
        x = max(0, min(center_x - w // 2, self.video_size[0] - w))
        y = max(0, min(center_y - h // 2, self.video_size[1] - h))
        self.crop = [x, y, w, h]
        self.update_crop_rect()
        self.update()

    def update_video_size(self, new_size):
        self.video_size = new_size
        self.crop = [0, 0, new_size[0], new_size[1]]
        self.update_crop_rect()
        self.update()

    def get_crop(self):
        return self.crop

    def update_crop_rect(self):
        parent = self.parent()
        parent_size = parent.size() if parent else QSize(self.video_size[0], self.video_size[1])
        w_disp, h_disp = parent_size.width(), parent_size.height()
        v_w, v_h = self.video_size
        ratio = min(w_disp / v_w, h_disp / v_h)
        scaled_w, scaled_h = int(v_w * ratio), int(v_h * ratio)
        offset_x = (w_disp - scaled_w) // 2
        offset_y = (h_disp - scaled_h) // 2
        self.ratio_w = ratio
        self.ratio_h = ratio
        x, y, crop_w, crop_h = self.crop
        self.crop_rect = QRect(
            offset_x + int(x * self.ratio_w),
            offset_y + int(y * self.ratio_h),
            int(crop_w * self.ratio_w), int(crop_h * self.ratio_h)
        )
        self.offset_x = offset_x
        self.offset_y = offset_y
        self.scaled_video_size = (scaled_w, scaled_h)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # Only draw the green outline and handles (no overlays)
        painter.setPen(QPen(Qt.green, 2, Qt.SolidLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self.crop_rect)

        # Draw corner handles for resizing
        rect = self.crop_rect
        handle_size = self.corner_size
        for pt in [rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight()]:
            painter.drawRect(QRect(pt, QSize(handle_size, handle_size)))

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return

        pos = event.pos()
        rect = self.crop_rect

        handle_size = self.corner_size
        corners = {
            'top-left': QRect(rect.topLeft(), QSize(handle_size, handle_size)),
            'top-right': QRect(rect.topRight() - QPoint(handle_size, 0), QSize(handle_size, handle_size)),
            'bottom-left': QRect(rect.bottomLeft() - QPoint(0, handle_size), QSize(handle_size, handle_size)),
            'bottom-right': QRect(rect.bottomRight() - QPoint(handle_size, handle_size), QSize(handle_size, handle_size)),
        }
        for edge, area in corners.items():
            if area.contains(pos):
                self.drag_edge = edge
                self.resizing = True
                break

        if not self.drag_edge and not self.aspect_ratio:
            if abs(pos.x() - rect.left()) < self.edge_margin and rect.top() <= pos.y() <= rect.bottom():
                self.drag_edge = 'left'
                self.resizing = True
            elif abs(pos.x() - rect.right()) < self.edge_margin and rect.top() <= pos.y() <= rect.bottom():
                self.drag_edge = 'right'
                self.resizing = True
            elif abs(pos.y() - rect.top()) < self.edge_margin and rect.left() <= pos.x() <= rect.right():
                self.drag_edge = 'top'
                self.resizing = True
            elif abs(pos.y() - rect.bottom()) < self.edge_margin and rect.left() <= pos.x() <= rect.right():
                self.drag_edge = 'bottom'
                self.resizing = True

        if not self.drag_edge and rect.contains(pos):
            self.dragging = True
            self.drag_offset = pos - rect.topLeft()

    def mouseReleaseEvent(self, event):
        self.dragging = False
        self.resizing = False
        self.drag_edge = None

    def mouseMoveEvent(self, event):
        pos = event.pos()
        rect = self.crop_rect

        handle_size = self.corner_size
        corners = {
            'top-left': QRect(rect.topLeft(), QSize(handle_size, handle_size)),
            'top-right': QRect(rect.topRight() - QPoint(handle_size, 0), QSize(handle_size, handle_size)),
            'bottom-left': QRect(rect.bottomLeft() - QPoint(0, handle_size), QSize(handle_size, handle_size)),
            'bottom-right': QRect(rect.bottomRight() - QPoint(handle_size, handle_size), QSize(handle_size, handle_size)),
        }
        cursor_map = {
            'top-left': Qt.SizeFDiagCursor,
            'bottom-right': Qt.SizeFDiagCursor,
            'top-right': Qt.SizeBDiagCursor,
            'bottom-left': Qt.SizeBDiagCursor,
        }
        in_handle = False
        for edge, area in corners.items():
            if area.contains(pos):
                self.setCursor(cursor_map[edge])
                in_handle = True
                break
        if not in_handle:
            if not self.aspect_ratio:
                if abs(pos.x() - rect.left()) < self.edge_margin and rect.top() <= pos.y() <= rect.bottom():
                    self.setCursor(Qt.SizeHorCursor)
                elif abs(pos.x() - rect.right()) < self.edge_margin and rect.top() <= pos.y() <= rect.bottom():
                    self.setCursor(Qt.SizeHorCursor)
                elif abs(pos.y() - rect.top()) < self.edge_margin and rect.left() <= pos.x() <= rect.right():
                    self.setCursor(Qt.SizeVerCursor)
                elif abs(pos.y() - rect.bottom()) < self.edge_margin and rect.left() <= pos.x() <= rect.right():
                    self.setCursor(Qt.SizeVerCursor)
                elif rect.contains(pos):
                    self.setCursor(Qt.SizeAllCursor)
                else:
                    self.setCursor(Qt.CrossCursor)
            else:
                if rect.contains(pos):
                    self.setCursor(Qt.SizeAllCursor)
                else:
                    self.setCursor(Qt.CrossCursor)

        if self.dragging:
            new_top_left = pos - self.drag_offset
            new_x = max(self.offset_x, min(new_top_left.x(), self.offset_x + self.scaled_video_size[0] - rect.width()))
            new_y = max(self.offset_y, min(new_top_left.y(), self.offset_y + self.scaled_video_size[1] - rect.height()))
            video_x = int((new_x - self.offset_x) / self.ratio_w)
            video_y = int((new_y - self.offset_y) / self.ratio_h)
            self.set_crop([video_x, video_y, self.crop[2], self.crop[3]])

        elif self.resizing and self.drag_edge:
            x, y, w, h = self.crop
            aspect = self.aspect_ratio if self.aspect_ratio else (w / h)
            min_size = self.min_size

            mouse_vx = int((pos.x() - self.offset_x) / self.ratio_w)
            mouse_vy = int((pos.y() - self.offset_y) / self.ratio_h)

            if self.drag_edge == 'top-left':
                new_x = min(mouse_vx, x + w - min_size)
                new_y = min(mouse_vy, y + h - min_size)
                new_w = w + (x - new_x)
                new_h = h + (y - new_y)
                if self.aspect_ratio:
                    new_w = max(min_size, new_w)
                    new_h = int(new_w / aspect)
                    if new_y + new_h > self.video_size[1]:
                        new_h = self.video_size[1] - new_y
                        new_w = int(new_h * aspect)
                    self.set_crop([new_x, new_y, new_w, new_h])
                else:
                    self.set_crop([new_x, new_y, new_w, new_h])

            elif self.drag_edge == 'top-right':
                new_x = x
                new_y = min(mouse_vy, y + h - min_size)
                new_w = max(min_size, mouse_vx - x)
                new_h = h + (y - new_y)
                if self.aspect_ratio:
                    new_w = max(min_size, new_w)
                    new_h = int(new_w / aspect)
                    if new_y + new_h > self.video_size[1]:
                        new_h = self.video_size[1] - new_y
                        new_w = int(new_h * aspect)
                    self.set_crop([new_x, new_y, new_w, new_h])
                else:
                    self.set_crop([new_x, new_y, new_w, new_h])

            elif self.drag_edge == 'bottom-left':
                new_x = min(mouse_vx, x + w - min_size)
                new_y = y
                new_w = w + (x - new_x)
                new_h = max(min_size, mouse_vy - y)
                if self.aspect_ratio:
                    new_w = max(min_size, new_w)
                    new_h = int(new_w / aspect)
                    if new_y + new_h > self.video_size[1]:
                        new_h = self.video_size[1] - new_y
                        new_w = int(new_h * aspect)
                    self.set_crop([new_x, new_y, new_w, new_h])
                else:
                    self.set_crop([new_x, new_y, new_w, new_h])

            elif self.drag_edge == 'bottom-right':
                new_x = x
                new_y = y
                new_w = max(min_size, mouse_vx - x)
                new_h = max(min_size, mouse_vy - y)
                if self.aspect_ratio:
                    new_w = max(min_size, new_w)
                    new_h = int(new_w / aspect)
                    if new_y + new_h > self.video_size[1]:
                        new_h = self.video_size[1] - new_y
                        new_w = int(new_h * aspect)
                    self.set_crop([new_x, new_y, new_w, new_h])
                else:
                    self.set_crop([new_x, new_y, new_w, new_h])

            elif not self.aspect_ratio:
                if self.drag_edge == 'left':
                    px = max(self.offset_x, min(pos.x(), self.offset_x + self.scaled_video_size[0]))
                    video_x = int((px - self.offset_x) / self.ratio_w)
                    new_x = min(video_x, x + w - min_size)
                    new_w = w + (x - new_x)
                    self.set_crop([new_x, y, new_w, h])
                elif self.drag_edge == 'right':
                    px = max(self.offset_x, min(pos.x(), self.offset_x + self.scaled_video_size[0]))
                    video_x = int((px - self.offset_x) / self.ratio_w)
                    new_w = max(self.min_size, min(video_x - x, self.video_size[0] - x))
                    self.set_crop([x, y, new_w, h])
                elif self.drag_edge == 'top':
                    py = max(self.offset_y, min(pos.y(), self.offset_y + self.scaled_video_size[1]))
                    video_y = int((py - self.offset_y) / self.ratio_h)
                    new_y = min(video_y, y + h - min_size)
                    new_h = h + (y - new_y)
                    self.set_crop([x, new_y, w, new_h])
                elif self.drag_edge == 'bottom':
                    py = max(self.offset_y, min(pos.y(), self.offset_y + self.scaled_video_size[1]))
                    video_y = int((py - self.offset_y) / self.ratio_h)
                    new_h = max(self.min_size, min(video_y - y, self.video_size[1] - y))
                    self.set_crop([x, y, w, new_h])

        self.update_crop_rect()
        self.update()

class VideoLabel(QLabel):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background: #111;")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_pixmap = None
        self.video_size = None

    def set_video(self, pixmap, video_size):
        self.video_pixmap = pixmap
        self.video_size = video_size
        self.repaint()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.video_pixmap and self.video_size:
            painter = QPainter(self)
            disp_w, disp_h = self.width(), self.height()
            v_w, v_h = self.video_size
            ratio = min(disp_w / v_w, disp_h / v_h)
            scaled_w, scaled_h = int(v_w * ratio), int(v_h * ratio)
            offset_x = (disp_w - scaled_w) // 2
            offset_y = (disp_h - scaled_h) // 2
            target = QRect(offset_x, offset_y, scaled_w, scaled_h)
            painter.drawPixmap(target, self.video_pixmap)
            painter.end()

class VideoCropper(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("EZ Crop & Crop")
        self.setAcceptDrops(True)
        self.video_path = None
        self.video_cap = None
        self.frame_count = 0
        self.fps = 1
        self.duration = 1
        self.video_size = (640, 360)
        self.aspect_mode = "Manual"
        self.playing = False
        self.crop_start = 0
        self.crop_end = None
        self.settings = QSettings("EZCropCrop", "EZCropCropApp")
        self.last_open_folder = self.settings.value("last_open_folder", os.path.expanduser("~"))
        self.last_save_folder = self.settings.value("last_save_folder", os.path.expanduser("~"))
        self.init_ui()

    def init_ui(self):
        self.video_label = VideoLabel()
        self.video_label.setMinimumSize(320, 180)
        self.video_label.setScaledContents(False)
        self.crop_overlay = CropOverlay(self.video_label, self.video_size)
        self.crop_overlay.setParent(self.video_label)
        self.crop_overlay.resize(self.video_label.size())
        self.crop_overlay.show()

        self.timeline_slider = QSlider(Qt.Horizontal)
        self.timeline_slider.setRange(0, 0)
        self.timeline_slider.valueChanged.connect(self.seek_frame)

        self.play_btn = QPushButton("Play")
        self.play_btn.clicked.connect(self.toggle_play)
        self.start_btn = QPushButton("Set Start")
        self.start_btn.clicked.connect(self.set_crop_start)
        self.end_btn = QPushButton("Set End")
        self.end_btn.clicked.connect(self.set_crop_end)
        self.load_btn = QPushButton("Load Video")
        self.load_btn.clicked.connect(self.load_video)
        self.export_btn = QPushButton("Export Crop")
        self.export_btn.clicked.connect(self.export_crop)

        self.crop_x = QLineEdit("0")
        self.crop_y = QLineEdit("0")
        self.crop_w = QLineEdit(str(self.video_size[0]))
        self.crop_h = QLineEdit(str(self.video_size[1]))
        for e in (self.crop_x, self.crop_y, self.crop_w, self.crop_h):
            e.setFixedWidth(50)
            e.editingFinished.connect(self.update_crop_from_fields)
        crop_fields = QHBoxLayout()
        crop_fields.addWidget(QLabel("X:")); crop_fields.addWidget(self.crop_x)
        crop_fields.addWidget(QLabel("Y:")); crop_fields.addWidget(self.crop_y)
        crop_fields.addWidget(QLabel("W:")); crop_fields.addWidget(self.crop_w)
        crop_fields.addWidget(QLabel("H:")); crop_fields.addWidget(self.crop_h)

        self.ratio_combo = QComboBox()
        self.ratio_combo.addItems(ASPECT_PRESETS.keys())
        self.ratio_combo.currentTextChanged.connect(self.change_aspect_mode)

        self.video_size_label = QLabel("Video: 0 x 0")
        self.crop_size_label = QLabel("Crop: 0 x 0")

        top_layout = QVBoxLayout()
        video_container = QVBoxLayout()
        video_container.addWidget(self.video_label)
        top_layout.addLayout(video_container)
        top_layout.addWidget(self.timeline_slider)
        controls = QHBoxLayout()
        controls.addWidget(self.play_btn)
        controls.addWidget(self.start_btn)
        controls.addWidget(self.end_btn)
        controls.addWidget(self.load_btn)
        controls.addWidget(self.export_btn)
        controls.addStretch()
        top_layout.addLayout(controls)
        fields = QHBoxLayout()
        fields.addLayout(crop_fields)
        fields.addWidget(QLabel("Preset:"))
        fields.addWidget(self.ratio_combo)
        fields.addWidget(self.video_size_label)
        fields.addWidget(self.crop_size_label)
        top_layout.addLayout(fields)

        widget = QWidget()
        widget.setLayout(top_layout)
        self.setCentralWidget(widget)

        self.timer = QTimer()
        self.timer.timeout.connect(self.next_frame)

        self.resize(1000, 700)
        self.show()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.crop_overlay.resize(self.video_label.size())
        self.crop_overlay.update_crop_rect()
        self.crop_overlay.update()
        self.show_frame(self.timeline_slider.value())

    def load_video(self, path=None):
        if not path:
            file, _ = QFileDialog.getOpenFileName(
                self, "Open Video", self.last_open_folder, "Video Files (*.mp4 *.mov *.avi *.mkv *.webm)"
            )
            if not file:
                return
            path = file
        self.last_open_folder = os.path.dirname(path)
        self.settings.setValue("last_open_folder", self.last_open_folder)
        self.settings.sync()
        self.video_path = path
        if self.video_cap:
            self.video_cap.release()
        self.video_cap = cv2.VideoCapture(path)
        self.frame_count = int(self.video_cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.fps = self.video_cap.get(cv2.CAP_PROP_FPS)
        self.duration = self.frame_count / self.fps
        w = int(self.video_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self.video_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.video_size = (w, h)
        self.crop_overlay.update_video_size(self.video_size)
        self.crop_overlay.crop = [0, 0, w, h]
        self.crop_overlay.resize(self.video_label.size())
        self.crop_overlay.update_crop_rect()
        self.crop_overlay.update()
        self.timeline_slider.setMaximum(self.frame_count - 1)
        self.timeline_slider.setValue(0)
        self.crop_start = 0
        self.crop_end = self.frame_count - 1
        self.video_size_label.setText(f"Video: {w} x {h}")
        self.update_crop_labels()
        self.show_frame(0)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and urls[0].toLocalFile().lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm')):
                event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            file_path = urls[0].toLocalFile()
            if os.path.exists(file_path):
                self.load_video(file_path)

    def show_frame(self, frame_idx):
        if not self.video_cap or frame_idx < 0 or frame_idx >= self.frame_count:
            return
        self.video_cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = self.video_cap.read()
        if not ret:
            return
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = frame_rgb.shape
        qimg = QImage(frame_rgb.data, w, h, ch * w, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg)
        self.video_label.set_video(pixmap, (w, h))
        self.crop_overlay.raise_()
        self.update_crop_labels()

    def seek_frame(self, value):
        self.show_frame(value)

    def next_frame(self):
        idx = self.timeline_slider.value()
        idx += 1
        if idx > self.crop_end or idx >= self.frame_count:
            self.playing = False
            self.timer.stop()
            self.play_btn.setText("Play")
            return
        self.timeline_slider.setValue(idx)

    def toggle_play(self):
        if not self.playing:
            self.playing = True
            self.timer.start(int(1000 / self.fps))
            self.play_btn.setText("Pause")
        else:
            self.playing = False
            self.timer.stop()
            self.play_btn.setText("Play")

    def set_crop_start(self):
        self.crop_start = self.timeline_slider.value()
        QMessageBox.information(self, "Set Start", f"Start time set to frame {self.crop_start}")

    def set_crop_end(self):
        self.crop_end = self.timeline_slider.value()
        QMessageBox.information(self, "Set End", f"End time set to frame {self.crop_end}")

    def change_aspect_mode(self, mode):
        aspect = ASPECT_PRESETS[mode]
        self.aspect_mode = mode
        self.crop_overlay.set_aspect_ratio(aspect)

    def update_crop_labels(self):
        x, y, w, h = self.crop_overlay.get_crop()
        self.crop_x.setText(str(x))
        self.crop_y.setText(str(y))
        self.crop_w.setText(str(w))
        self.crop_h.setText(str(h))
        self.crop_size_label.setText(f"Crop: {w} x {h}")

    def update_crop_from_fields(self):
        try:
            x = int(self.crop_x.text())
            y = int(self.crop_y.text())
            w = int(self.crop_w.text())
            h = int(self.crop_h.text())
        except ValueError:
            return
        self.crop_overlay.set_crop([x, y, w, h])
        self.update_crop_labels()

    def export_crop(self):
        if not self.video_path:
            QMessageBox.warning(self, "No video loaded", "Please load a video first.")
            return

        base, ext = os.path.splitext(os.path.basename(self.video_path))
        default_name = base + "-cropped" + ext
        default_path = os.path.join(self.last_save_folder, default_name)
        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Cropped Video",
            default_path,
            "Video Files (*%s)" % ext
        )
        if not out_path:
            return
        # Ensure extension is present
        if not out_path.lower().endswith(ext.lower()):
            out_path += ext
        self.last_save_folder = os.path.dirname(out_path)
        self.settings.setValue("last_save_folder", self.last_save_folder)
        self.settings.sync()

        x, y, w, h = self.crop_overlay.get_crop()
        if w <= 0 or h <= 0:
            QMessageBox.warning(self, "Invalid Crop", "Crop width and height must be positive.")
            return
        if x < 0 or y < 0 or x + w > self.video_size[0] or y + h > self.video_size[1]:
            QMessageBox.warning(self, "Invalid Crop", "Crop rectangle is out of video bounds.")
            return
        start_sec = self.crop_start / self.fps
        duration_sec = (self.crop_end - self.crop_start + 1) / self.fps
        if duration_sec <= 0:
            QMessageBox.warning(self, "Invalid Range", "End frame must be after start frame.")
            return
        cmd = [
            "ffmpeg",
            "-y",
            "-i", self.video_path,
            "-ss", f"{start_sec:.3f}",
            "-t", f"{duration_sec:.3f}",
            "-filter:v", f"crop={w}:{h}:{x}:{y}",
            "-c:a", "copy",
            out_path
        ]
        print(" ".join(cmd))
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode == 0:
            QMessageBox.information(self, "Export Done", "Video exported successfully!")
        else:
            err = proc.stderr.decode()
            lines = err.splitlines()
            preview = "\n".join(lines[:20])
            QMessageBox.warning(self, "Export Failed", preview if preview else err)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    win = VideoCropper()
    sys.exit(app.exec_())
