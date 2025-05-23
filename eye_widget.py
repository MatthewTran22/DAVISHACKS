# eye_widget.py

import time
import cv2
import mediapipe as mp
import numpy as np
import pyautogui

from PyQt5.QtWidgets import (
    QWidget, QLabel, QPushButton,
    QVBoxLayout, QDialog, QFormLayout,
    QDoubleSpinBox, QSpinBox
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap

class EyeTrackerWidget(QWidget):
    calibration_complete = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setFocusPolicy(Qt.StrongFocus)

        # ─ Calibration state ─────────────────────────────
        self.calibrated = False
        self.calibration_points = []
        self.calibration_step = 0
        self.instructions = [
            "Look at TOP‑LEFT and double‑blink",
            "Look at TOP‑RIGHT and double‑blink",
            "Look at BOTTOM‑RIGHT and double‑blink",
            "Look at BOTTOM‑LEFT and double‑blink",
            "Look at CENTER and double‑blink",
        ]

        # ─ Blink detection ───────────────────────────────
        self.LEFT_EYE  = [33,160,158,133,153,144]
        self.RIGHT_EYE = [362,385,387,263,373,380]
        self.blink_thresh = 0.21
        self.is_blinking = False
        self.blink_times = []
        self.double_window = 0.5
        self.last_blink = 0
        self.ignore_after_blink = 0.3

        # ─ Gaze smoothing & dwell ────────────────────────
        self.smoothed = None
        self.move_thr = 20
        self.alpha    = 0.3
        self.dwell    = 1.0
        self.dwell_ts = 0
        self.last_click = 0

        # ─ UI setup ───────────────────────────────────────
        self.instruction_label = QLabel(self.instructions[0], alignment=Qt.AlignCenter)
        self.video_label       = QLabel(alignment=Qt.AlignCenter)
        self.recalib_btn       = QPushButton("Re‑calibrate")
        self.recalib_btn.clicked.connect(self.start_recalib)
        self.settings_btn      = QPushButton("Settings")
        self.settings_btn.clicked.connect(self.open_settings)

        layout = QVBoxLayout(self)
        layout.addWidget(self.instruction_label)
        layout.addWidget(self.video_label, 1)
        layout.addWidget(self.recalib_btn)
        layout.addWidget(self.settings_btn)

        # ─ Frame update timer ─────────────────────────────
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update)

        # ─ after calibration, shrink the preview ─────────
        self.calibration_complete.connect(self._on_calibrated)

    def start_tracking(self):
        self.cap = cv2.VideoCapture(0)
        self.face_mesh = mp.solutions.face_mesh.FaceMesh(refine_landmarks=True)
        self.timer.start(30)

    def stop_tracking(self):
        self.timer.stop()
        if hasattr(self, 'cap') and self.cap:
            self.cap.release()
            self.cap = None

    def _get_iris(self, lm):
        p = lm[468]
        return p.x, p.y

    def _map(self, ix, iy):
        pts = self.calibration_points[:4]
        minx, maxx = min(p[0] for p in pts), max(p[0] for p in pts)
        miny, maxy = min(p[1] for p in pts), max(p[1] for p in pts)
        ix = max(minx, min(maxx, ix))
        iy = max(miny, min(maxy, iy))
        # invert horizontal so head-left → cursor-left
        rx = (ix - minx) / (maxx - minx) if maxx != minx else 0.5
        ry =     (iy - miny) / (maxy - miny) if maxy != miny else 0.5
        sw, sh = pyautogui.size()
        return int(rx * (sw - 1)), int(ry * (sh - 1))

    def _ear(self, lm, ids):
        pts = [lm[i] for i in ids]
        A = np.linalg.norm([pts[1].x-pts[5].x, pts[1].y-pts[5].y])
        B = np.linalg.norm([pts[2].x-pts[4].x, pts[2].y-pts[4].y])
        C = np.linalg.norm([pts[0].x-pts[3].x, pts[0].y-pts[3].y])
        return (A+B)/(2*C) if C else 0

    def _update(self):
        ret, frame = self.cap.read()
        if not ret:
            return

        frame = cv2.flip(frame, 1)  # mirror so it feels natural
        rgb   = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        res   = self.face_mesh.process(rgb)
        now   = time.time()

        if res.multi_face_landmarks:
            lm  = res.multi_face_landmarks[0].landmark
            ear = (self._ear(lm,self.LEFT_EYE) + self._ear(lm,self.RIGHT_EYE)) / 2

            # detect blink edge
            if ear < self.blink_thresh and not self.is_blinking:
                self.is_blinking = True
            elif ear >= self.blink_thresh and self.is_blinking:
                self.is_blinking = False
                self.last_blink = now

                if not self.calibrated:
                    # double blink → calibration
                    self.blink_times.append(now)
                    if len(self.blink_times) > 2:
                        self.blink_times.pop(0)
                    if (len(self.blink_times)==2 and
                        self.blink_times[1]-self.blink_times[0]<self.double_window):
                        self.blink_times.clear()
                        ix, iy = self._get_iris(lm)
                        self.calibration_points.append((ix, iy))
                        self.calibration_step += 1
                        if self.calibration_step < len(self.instructions):
                            self.instruction_label.setText(self.instructions[self.calibration_step])
                        else:
                            self.calibrated = True
                            self.instruction_label.setText("Calibration complete!")
                            self.calibration_complete.emit()
                else:
                    # after calibration, single blink → click
                    pyautogui.click()

            # gaze → cursor & dwell, skip immediately after blink
            if self.calibrated and (now - self.last_blink) > self.ignore_after_blink:
                ix, iy = self._get_iris(lm)
                tx, ty = self._map(ix, iy)
                if self.smoothed is None:
                    self.smoothed, self.dwell_ts = (tx, ty), now
                else:
                    dx, dy = tx - self.smoothed[0], ty - self.smoothed[1]
                    dist = (dx*dx + dy*dy)**0.5
                    if dist < self.move_thr:
                        if now - self.dwell_ts > self.dwell and now - self.last_click > self.dwell:
                            pyautogui.click()
                            self.last_click = now
                    else:
                        self.dwell_ts = now
                        α = min(0.85, max(self.alpha, dist/300))
                        sx = int(α*tx + (1-α)*self.smoothed[0])
                        sy = int(α*ty + (1-α)*self.smoothed[1])
                        self.smoothed = (sx, sy)
                        pyautogui.moveTo(sx, sy, duration=0.08)

        # show preview
        h, w, _ = frame.shape
        img = QImage(frame.data, w, h, 3*w, QImage.Format_RGB888).rgbSwapped()
        self.video_label.setPixmap(QPixmap.fromImage(img))

    def start_recalib(self):
        self.calibrated = False
        self.calibration_points.clear()
        self.calibration_step = 0
        self.smoothed = None
        self.instruction_label.setText(self.instructions[0])

    def open_settings(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Settings")
        form = QFormLayout(dlg)
        dwell = QDoubleSpinBox(); dwell.setRange(0.2,3.0); dwell.setValue(self.dwell)
        dwell.valueChanged.connect(lambda v: setattr(self,'dwell',v))
        form.addRow("Dwell (s):", dwell)
        thr = QSpinBox(); thr.setRange(1,100); thr.setValue(self.move_thr)
        thr.valueChanged.connect(lambda v: setattr(self,'move_thr',v))
        form.addRow("Move threshold:", thr)
        αspin = QDoubleSpinBox(); αspin.setRange(0.01,0.99); αspin.setSingleStep(0.01)
        αspin.setValue(self.alpha); αspin.valueChanged.connect(lambda v: setattr(self,'alpha',v))
        form.addRow("Smoothing α:", αspin)
        btn = QPushButton("Close"); btn.clicked.connect(dlg.close)
        form.addRow(btn)
        dlg.exec_()

    def _on_calibrated(self):
        # shrink the preview widget down into a corner
        self.video_label.setFixedSize(200, 150)
        self.video_label.setScaledContents(True)

    def closeEvent(self, event):
        self.stop_tracking()
        super().closeEvent(event)