import base64
import asyncio
import sys
import threading
import winreg
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox, QVBoxLayout, QWidget


class DesktopBridge(QObject):
    """Methods called from desktop_pet.js through Qt WebChannel."""

    speech_started = Signal()
    speech_interim = Signal(str)
    speech_final = Signal(str)
    speech_state = Signal(str)
    speech_error = Signal(str)
    speech_privacy_required = Signal()
    microphone_permission_required = Signal()

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.selected_screen = None
        self._voice_stop = threading.Event()
        self._voice_thread = None
        self.speech_privacy_required.connect(self._show_speech_privacy_dialog)
        self.microphone_permission_required.connect(self._show_microphone_dialog)

    @Slot(result=bool)
    def start_drag(self):
        handle = self.window.windowHandle()
        return bool(handle and handle.startSystemMove())

    @Slot(result=bool)
    def close_window(self):
        self.window.close()
        return True

    @Slot(result=bool)
    def request_screen_access(self):
        screens = QApplication.screens()
        if not screens:
            return False
        labels = []
        for index, screen in enumerate(screens):
            geometry = screen.geometry()
            labels.append(
                f"屏幕 {index + 1} · {screen.name()} · {geometry.width()}×{geometry.height()}"
            )
        selected, accepted = QInputDialog.getItem(
            self.window,
            "开启陪伴模式",
            "选择允许 LightMe 按需读取的屏幕：",
            labels,
            0,
            False,
        )
        if not accepted:
            return False
        self.selected_screen = screens[labels.index(selected)]
        return True

    @Slot(result=str)
    def capture_screen(self):
        screen = self.selected_screen
        if screen is None:
            return ""
        pixmap = screen.grabWindow(0)
        if pixmap.isNull():
            return ""
        if pixmap.width() > 1280 or pixmap.height() > 1280:
            pixmap = pixmap.scaled(
                1280,
                1280,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.save(buffer, "JPG", 68)
        buffer.close()
        return base64.b64encode(bytes(data)).decode("ascii")

    @Slot()
    def stop_screen_access(self):
        self.selected_screen = None

    @Slot(result=bool)
    def start_voice_input(self):
        if not self._speech_privacy_accepted():
            self._show_speech_privacy_dialog()
            return False
        if self._voice_thread and self._voice_thread.is_alive():
            return True
        self._voice_stop.clear()
        self._voice_thread = threading.Thread(
            target=self._run_voice_worker,
            name="LightMeNativeSpeech",
            daemon=True,
        )
        self._voice_thread.start()
        return True

    @Slot(result=bool)
    def ensure_speech_permission(self):
        if self._speech_privacy_accepted():
            return True
        self._show_speech_privacy_dialog()
        return False

    @Slot()
    def stop_voice_input(self):
        self._voice_stop.set()

    def _run_voice_worker(self):
        try:
            asyncio.run(self._recognize_continuously())
        except Exception as error:
            message = str(error)
            lowered = message.lower()
            if "privacy policy" in lowered or "internal speech error" in lowered:
                self.speech_privacy_required.emit()
                self.speech_error.emit("speech-privacy-not-accepted")
            elif "access" in lowered and ("denied" in lowered or "权限" in message):
                self.microphone_permission_required.emit()
                self.speech_error.emit("microphone-access-denied")
            else:
                self.speech_error.emit(message)
            self.speech_state.emit("idle")

    @staticmethod
    def _speech_privacy_accepted():
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Speech_OneCore\Settings\OnlineSpeechPrivacy",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "HasAccepted")
                return int(value) == 1
        except (FileNotFoundError, OSError, ValueError):
            return False

    @Slot()
    def _show_speech_privacy_dialog(self):
        answer = QMessageBox.question(
            self.window,
            "需要开启 Windows 语音权限",
            "陪伴模式需要 Windows 的“在线语音识别”。\n\n"
            "点击“是”打开系统设置，把“在线语音识别”开关打开。\n"
            "设置完成后请关闭并重新启动桌宠，再点击“语音”按钮。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl("ms-settings:privacy-speech"))

    @Slot()
    def _show_microphone_dialog(self):
        answer = QMessageBox.question(
            self.window,
            "需要开启麦克风权限",
            "请允许桌面应用访问麦克风。\n\n点击“是”打开 Windows 麦克风隐私设置。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl("ms-settings:privacy-microphone"))

    async def _recognize_continuously(self):
        from winrt.windows.media.speechrecognition import SpeechRecognizer

        while not self._voice_stop.is_set():
            recognizer = SpeechRecognizer()
            compile_result = await recognizer.compile_constraints_async()
            if int(compile_result.status) != 0:
                recognizer.close()
                raise RuntimeError(f"语音识别初始化失败：{compile_result.status}")

            session = recognizer.continuous_recognition_session
            loop = asyncio.get_running_loop()
            session_completed = asyncio.Event()
            speech_active = False

            def on_hypothesis(_, args):
                nonlocal speech_active
                text = str(args.hypothesis.text or "").strip()
                if not text:
                    return
                if not speech_active:
                    speech_active = True
                    self.speech_started.emit()
                    self.speech_state.emit("speech")
                self.speech_interim.emit(text)

            def on_result(_, args):
                nonlocal speech_active
                text = str(args.result.text or "").strip()
                speech_active = False
                if text:
                    self.speech_final.emit(text)
                    self.speech_state.emit("processing")

            def on_completed(_, __):
                loop.call_soon_threadsafe(session_completed.set)

            hypothesis_token = recognizer.add_hypothesis_generated(on_hypothesis)
            result_token = session.add_result_generated(on_result)
            completed_token = session.add_completed(on_completed)
            started = False
            try:
                await session.start_async()
                started = True
                self.speech_state.emit("listening")
                while not self._voice_stop.is_set() and not session_completed.is_set():
                    await asyncio.sleep(0.1)
            finally:
                if started:
                    try:
                        await session.stop_async()
                    except Exception:
                        pass
                recognizer.remove_hypothesis_generated(hypothesis_token)
                session.remove_result_generated(result_token)
                session.remove_completed(completed_token)
                recognizer.close()

            if not self._voice_stop.is_set():
                self.speech_state.emit("reconnecting")
                await asyncio.sleep(0.35)

        self.speech_state.emit("idle")


class DesktopPetWindow(QWidget):
    def __init__(self, url):
        super().__init__()
        self.setWindowTitle("LightMe 桌面宠物")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setStyleSheet("background: transparent;")
        self.setFixedSize(220, 340)

        self.webview = QWebEngineView(self)
        self.webview.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.webview.setStyleSheet("background: transparent;")
        self.webview.page().setBackgroundColor(QColor(0, 0, 0, 0))

        settings = self.webview.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True
        )

        profile = QWebEngineProfile.defaultProfile()
        cache_root = Path.home() / ".lightme" / "qtwebengine"
        cache_root.mkdir(parents=True, exist_ok=True)
        profile.setCachePath(str(cache_root / "cache"))
        profile.setPersistentStoragePath(str(cache_root / "storage"))

        self.bridge = DesktopBridge(self)
        self.channel = QWebChannel(self.webview.page())
        self.channel.registerObject("desktopBridge", self.bridge)
        self.webview.page().setWebChannel(self.channel)

        self.webview.page().featurePermissionRequested.connect(
            self._on_feature_permission_requested
        )
        desktop_media_signal = getattr(
            self.webview.page(), "desktopMediaRequested", None
        )
        if desktop_media_signal is not None:
            desktop_media_signal.connect(self._on_desktop_media_requested)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.webview)
        self.webview.load(QUrl(url))

    def closeEvent(self, event):
        self.bridge.stop_voice_input()
        self.bridge.stop_screen_access()
        super().closeEvent(event)

    @Slot(QUrl, QWebEnginePage.Feature)
    def _on_feature_permission_requested(self, origin, feature):
        feature_names = (
            "MediaAudioCapture",
            "MediaVideoCapture",
            "MediaAudioVideoCapture",
            "DesktopVideoCapture",
            "DesktopAudioVideoCapture",
        )
        allowed = {
            getattr(QWebEnginePage.Feature, name, None) for name in feature_names
        }
        policy = (
            QWebEnginePage.PermissionPolicy.PermissionGrantedByUser
            if feature in allowed
            else QWebEnginePage.PermissionPolicy.PermissionDeniedByUser
        )
        self.webview.page().setFeaturePermission(origin, feature, policy)

    def _on_desktop_media_requested(self, request):
        choices = []
        for kind, accessor in (
            ("屏幕", "screensModel"),
            ("窗口", "windowsModel"),
        ):
            model_getter = getattr(request, accessor, None)
            if model_getter is None:
                continue
            model = model_getter()
            for row in range(model.rowCount()):
                index = model.index(row, 0)
                label = str(model.data(index, Qt.ItemDataRole.DisplayRole) or f"{kind} {row + 1}")
                choices.append((f"{kind} · {label}", kind, index))

        if not choices:
            request.cancel()
            return

        labels = [item[0] for item in choices]
        selected, accepted = QInputDialog.getItem(
            self,
            "开启陪伴模式",
            "选择允许 LightMe 按需读取的屏幕或窗口：",
            labels,
            0,
            False,
        )
        if not accepted:
            request.cancel()
            return

        _, kind, index = choices[labels.index(selected)]
        if kind == "屏幕":
            request.selectScreen(index)
        else:
            request.selectWindow(index)


def main():
    url = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "http://127.0.0.1:8000/web/html/desktop_pet.html"
    )
    app = QApplication(sys.argv[:1])
    app.setApplicationName("LightMe 桌面宠物")
    app.setQuitOnLastWindowClosed(True)

    window = DesktopPetWindow(url)
    window.show()
    window.raise_()
    window.activateWindow()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
