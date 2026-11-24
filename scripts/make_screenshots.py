"""Render the screenshots used in the documentation, without needing a visible display.

Run from the RayCrafter repository so that its environment is used:

    cd path/to/raycrafter
    QT_QPA_PLATFORM=offscreen uv run python path/to/raycrafter-docs/scripts/make_screenshots.py \
        path/to/raycrafter-docs/img
"""

import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from raycrafter.gui.style import force_light_colour_scheme
from raycrafter.gui.main_window import MainWindow
from raycrafter.gui.lens_picker import LensPickerDialog


def settle(app: QApplication, window: MainWindow) -> None:
    """Let pending timers and redraws finish, including the slow analysis plots."""
    window.update_timer.stop()
    window.slow_timer.stop()
    window.recompute_and_redraw()
    window.update_slow_plots()
    for _ in range(5):
        app.processEvents()


def save(widget, output_dir: Path, name: str) -> None:
    """Save a screenshot of one widget as a PNG."""
    widget.grab().save(str(output_dir / f"{name}.png"))
    print("wrote", name)


def main(output_dir: Path) -> None:
    app = QApplication(sys.argv)
    force_light_colour_scheme(app)

    window = MainWindow()
    window.model.delete_lens(0)  # start from an empty bench
    window.model.add_lens("AC254-100-A")
    window.model.add_lens("AC254-050-A")
    second_field = window.model.add_field()
    second_field.angle_deg = 3.0
    second_field.colour = "#d62728"
    window.model.select_field(0)
    window.model.autofocus()
    window.side_view.rebuild_lens_graphics()
    window.show()
    settle(app, window)
    save(window, output_dir, "main-window")

    window.mtf_tabs.setCurrentIndex(0)
    window.spot_tabs.setCurrentIndex(1)
    settle(app, window)
    save(window, output_dir, "through-focus")

    window.open_field_editor()
    window.field_editor.add_button_for_screenshot = None
    settle(app, window)
    save(window.field_editor, output_dir, "field-window")

    picker = LensPickerDialog(window.model.catalog, parent=window)
    picker.search_box.setText("AC254-1")
    picker.show()
    app.processEvents()
    save(picker, output_dir, "lens-picker")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
