from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from arch_gui_installer.gui.wizard import Wizard


def test_wizard_event_loop_and_shutdown(monkeypatch):
    # Exercise the actual event loop so startup singleShot and worker signals finish.
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(Wizard, "scan", lambda self: None)
    w = Wizard(True)
    w.show()
    assert w.stack.count() == 11
    w.language.setCurrentIndex(1)
    assert w.next.text() == "Next"
    w.task(lambda: "done", lambda value: w.status.setText(value))

    def check():
        if w.tasks:
            QTimer.singleShot(10, check)
        else:
            assert w.status.text() == "done"
            w.close()
            app.quit()

    QTimer.singleShot(30, check)
    QTimer.singleShot(5000, app.quit)
    app.exec()
    w.shutdown_tasks()
    assert not w.tasks


def test_preview_never_starts_privileged_process(monkeypatch):
    from arch_gui_installer.core.config import DeviceIdentity
    from arch_gui_installer.hardware.detect import Disk
    from arch_gui_installer.storage.plan import make_plan

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(Wizard, "scan", lambda self: None)
    w = Wizard(True)
    w.hw = {"firmware": "uefi"}
    w.disk_box.addItem(
        "Test disk", Disk(DeviceIdentity("/dev/vdz", "9:0", 32 * 1024**3))
    )
    w.user_groups.setText("audio, video")
    c = w.config()
    c.validate()
    assert c.groups == ["audio", "video"]
    w.plan = make_plan(c, ["base"], {})
    w.begin()
    assert w.stack.currentIndex() == 10
    assert w.proc is None
    assert "DRY RUN" in w.logs.toPlainText()
    w.close()
    app.processEvents()


def test_verified_system_with_unmount_failure_is_not_reported_as_complete(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(Wizard, "scan", lambda self: None)
    monkeypatch.setattr(Wizard, "process_output", lambda self: None)
    w = Wizard(True)
    w.finalization_required = True
    w.process_finished(1, None)
    assert "System zweryfikowany" in w.status.text()
    assert not w.reboot.isEnabled()
    w.close()
    app.processEvents()
