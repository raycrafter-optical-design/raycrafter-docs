"""Build python-api.html by running every example on it in a real IPython session.

The text and the code of the page are in ``api_page_source.html``. Each ``<ipython>`` block
there is run, in order, in one session, and replaced by a transcript of what IPython printed.
Figures, and screenshots of the window, are saved to ``img/``. Edit the source, never
``python-api.html``.

Directives, each on its own line inside a block:

    #figure NAME       save the matplotlib figure that the previous command returned
    #screenshot NAME   save pictures of the window (NAME.png) and its Bench list, if open
    #bench-viewer      open the window's "Bench" list and select the second lens, as a user would

Run from the RayCrafter repository so that its environment (plus IPython) is used:

    cd path/to/raycrafter
    uv run --with ipython python path/to/raycrafter-docs/scripts/make_api_page.py

No display is needed: the window is drawn off screen.
"""

import html
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from IPython.core.interactiveshell import InteractiveShell
from IPython.utils.capture import capture_output
from matplotlib.figure import Figure
from traitlets.config import Config

DOCS = Path(__file__).resolve().parent.parent
SOURCE = DOCS / "scripts" / "api_page_source.html"
WINDOW_TIMEOUT_S = 120


def run_window(work_dir: Path) -> None:
    """Child process: a RayCrafter window with the agent link on, taking requested pictures.

    A request is a file in ``work_dir`` called ``NAME.shot`` or ``NAME.viewer``; it is
    deleted once carried out.
    """
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    from raycrafter.gui.agent_link import AgentLink
    from raycrafter.gui.main_window import MainWindow
    from raycrafter.gui.style import force_light_colour_scheme

    app = QApplication(sys.argv)
    force_light_colour_scheme(app)
    window = MainWindow()
    link = AgentLink(window)
    link.start()
    window.show()

    def serve_requests() -> None:
        for request in work_dir.glob("*.viewer"):
            window.open_bench_viewer()
            viewer = window.bench_viewer
            viewer.tree.setCurrentItem(viewer.lens_section.child(1))  # as if clicked
            request.unlink()
        for request in work_dir.glob("*.shot"):
            window.update_timer.stop()
            window.slow_timer.stop()
            window.recompute_and_redraw()
            window.update_slow_plots()
            for _ in range(5):
                app.processEvents()
            name = request.stem
            window.grab().save(str(DOCS / "img" / f"{name}.png"))
            if window.bench_viewer is not None and window.bench_viewer.isVisible():
                window.bench_viewer.grab().save(str(DOCS / "img" / f"{name}-bench.png"))
            request.unlink()

    timer = QTimer()
    timer.timeout.connect(serve_requests)
    timer.start(50)
    app.exec()


class Session:
    """One IPython session, plus the window it connects to."""

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        config = Config()
        config.HistoryManager.enabled = False
        config.InteractiveShell.colors = "nocolor"
        config.InteractiveShell.xmode = "Minimal"
        self.shell = InteractiveShell.instance(config=config)
        self.last_result = None
        self.window_process = None

    def window_request(self, file_name: str) -> None:
        """Ask the window process for something and wait until it has done it."""
        request = self.work_dir / file_name
        request.touch()
        self.wait_until(lambda: not request.exists())

    def wait_until(self, condition) -> None:
        deadline = time.monotonic() + WINDOW_TIMEOUT_S
        while not condition():
            if self.window_process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("The RayCrafter window process failed")
            time.sleep(0.05)

    def start_window(self) -> None:
        link_file = Path(os.environ["RAYCRAFTER_LINK_FILE"])
        self.window_process = subprocess.Popen(
            [sys.executable, __file__, "--window", str(self.work_dir)],
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        )
        self.wait_until(link_file.exists)

    def run_block(self, block: str) -> str:
        """Run one <ipython> block and return the HTML that replaces it."""
        pieces, transcript = [], []

        def flush() -> None:
            if transcript:
                text = html.escape("\n\n".join(transcript))
                text = re.sub(
                    r"^(In \[\d+\]:)", r'<span class="in">\1</span>', text, flags=re.M
                )
                text = re.sub(
                    r"^(Out\[\d+\]:)", r'<span class="out">\1</span>', text, flags=re.M
                )
                pieces.append(f'<pre class="ipython"><code>{text}</code></pre>')
                transcript.clear()

        def picture(name: str, alt: str, small: bool = False) -> None:
            flush()
            css = ' class="small"' if small else ""
            pieces.append(
                f'<figure{css}><a href="img/{name}.png" title="Open full size">'
                f'<img src="img/{name}.png" alt="{alt}" loading="lazy"></a></figure>'
            )

        for cell in split_cells(block):
            if cell.startswith("#figure "):
                name = cell.split()[1]
                assert isinstance(self.last_result, Figure), f"{name}: no figure to save"
                self.last_result.savefig(DOCS / "img" / f"{name}.png", dpi=130, bbox_inches="tight")
                picture(name, name.removeprefix("api-").replace("-", " "))
            elif cell.startswith("#screenshot "):
                name = cell.split()[1]
                self.window_request(f"{name}.shot")
                picture(name, "The RayCrafter window")
                if (DOCS / "img" / f"{name}-bench.png").exists():
                    picture(f"{name}-bench", "The Bench list", small=True)
            elif cell == "#bench-viewer":
                self.window_request("open.viewer")
            else:
                transcript.append(self.run_cell(cell))
        flush()
        return "\n".join(pieces)

    def run_cell(self, code: str) -> str:
        """Run one command and return it as it looks in an IPython terminal."""
        if "Bench.connect()" in code and self.window_process is None:
            self.start_window()
        number = self.shell.execution_count
        with capture_output() as captured:
            result = self.shell.run_cell(code, store_history=True)
        self.last_result = result.result
        expected_to_fail = "# raises" in code
        if result.success == expected_to_fail:
            raise RuntimeError(f"Unexpected result from:\n{code}\n{captured.stdout}")

        prompt = f"In [{number}]: "
        lines = code.split("\n")
        shown = prompt + lines[0]
        for line in lines[1:]:
            shown += "\n" + "...: ".rjust(len(prompt)) + line
        output = (captured.stdout + captured.stderr).rstrip()
        # A terminal shows only "<Figure ...>"; the page shows the picture instead.
        if isinstance(result.result, Figure):
            output = ""
        return shown + ("\n" + output if output else "")

    def close(self) -> None:
        if self.window_process is not None:
            self.window_process.terminate()
            self.window_process.wait()


def split_cells(block: str) -> list[str]:
    """Commands in a block are separated by blank lines; directives stand alone."""
    cells = []
    for chunk in re.split(r"\n\s*\n", block.strip("\n")):
        code = []
        for line in chunk.split("\n"):
            if line.startswith(("#figure ", "#screenshot ", "#bench-viewer")):
                if code:
                    cells.append("\n".join(code))
                    code = []
                cells.append(line.strip())
            else:
                code.append(line)
        if code:
            cells.append("\n".join(code))
    return cells


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        work_dir = Path(temporary)
        os.environ["RAYCRAFTER_LINK_FILE"] = str(work_dir / "agent_link.json")
        # A fixed, tidy working folder, because some outputs show full paths.
        demo_dir = Path("/tmp/raycrafter-demo")
        demo_dir.mkdir(exist_ok=True)
        os.chdir(demo_dir)
        for old in (DOCS / "img").glob("api-*.png"):
            old.unlink()
        session = Session(work_dir)
        try:
            page = re.sub(
                r"<ipython>\n(.*?)</ipython>",
                lambda match: session.run_block(match.group(1)),
                SOURCE.read_text(encoding="utf-8"),
                flags=re.S,
            )
        finally:
            session.close()
    (DOCS / "python-api.html").write_text(page, encoding="utf-8")
    print("wrote python-api.html")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--window"]:
        run_window(Path(sys.argv[2]))
    else:
        main()
