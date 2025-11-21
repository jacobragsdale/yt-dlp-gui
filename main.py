from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk, simpledialog
try:
    from tkinter import dnd
except Exception:  # noqa: BLE001
    dnd = None

import yt_dlp
from yt_dlp.extractor.youtube import YoutubeIE

OUTPUT_DIR = Path("downloads")
MAX_WORKERS = 4  # Bump up or down based on your bandwidth/CPU budget.
ARCHIVE_FILE = OUTPUT_DIR / "downloaded.txt"


def find_existing_path(url: str, dest_dir: Path = OUTPUT_DIR) -> Optional[Path]:
    """Return an existing downloaded file path for this URL, if present."""
    try:
        video_id = YoutubeIE.extract_id(url)
    except Exception:
        return None
    for path in dest_dir.glob(f"{video_id} - *.mp3"):
        return path
    return None


if __name__ == "__main__":
    def launch_gui() -> None:
        class DownloadApp(tk.Tk):
            def __init__(self) -> None:
                super().__init__()
                self.title("YouTube Audio Downloader")
                self.geometry("900x500")
                self.download_dir = OUTPUT_DIR.resolve()
                self.item_paths: Dict[str, Optional[Path]] = {}
                self.item_progress: Dict[str, float] = {}
                self.cancelled = False

                self._build_ui()

            def _build_ui(self) -> None:
                # Entry + add/remove
                entry_frame = ttk.Frame(self)
                entry_frame.pack(fill="x", padx=10, pady=10)

                ttk.Label(entry_frame, text="YouTube URL").pack(side="left")
                self.url_var = tk.StringVar()
                entry = ttk.Entry(entry_frame, textvariable=self.url_var)
                entry.pack(side="left", fill="x", expand=True, padx=8)
                entry.bind("<Return>", lambda _: self.add_url())

                ttk.Button(entry_frame, text="Add", command=self.add_url).pack(side="left", padx=4)
                ttk.Button(entry_frame, text="Add Multiple", command=self.add_multiple).pack(side="left", padx=4)
                ttk.Button(entry_frame, text="Remove Selected", command=self.remove_selected).pack(side="left", padx=4)
                if dnd:
                    try:
                        entry.drop_target_register(dnd.DND_TEXT)
                        entry.dnd_bind("<<Drop>>", self._on_drop)
                    except Exception:
                        pass

                # Folder chooser
                folder_frame = ttk.Frame(self)
                folder_frame.pack(fill="x", padx=10, pady=5)
                ttk.Button(folder_frame, text="Choose Folder", command=self.choose_folder).pack(side="left")
                self.folder_label = ttk.Label(folder_frame, text=str(self.download_dir))
                self.folder_label.pack(side="left", padx=8)

                # Treeview for downloads
                columns = ("title", "url", "progress", "status", "show", "retry")
                self.tree = ttk.Treeview(self, columns=columns, show="headings", height=15)
                self.tree.heading("title", text="Title")
                self.tree.heading("url", text="URL")
                self.tree.heading("progress", text="Progress")
                self.tree.heading("status", text="Status")
                self.tree.heading("show", text="Show")
                self.tree.heading("retry", text="Retry")
                self.tree.column("title", width=230)
                self.tree.column("url", width=310)
                self.tree.column("progress", width=80, anchor="center")
                self.tree.column("status", width=150)
                self.tree.column("show", width=60, anchor="center")
                self.tree.column("retry", width=60, anchor="center")
                self.tree.pack(fill="both", expand=True, padx=10, pady=10)
                self.tree.bind("<ButtonRelease-1>", self._handle_click)

                # Action buttons
                btn_frame = ttk.Frame(self)
                btn_frame.pack(fill="x", padx=10, pady=10)
                self.download_btn = ttk.Button(btn_frame, text="Download", command=self.start_downloads)
                self.download_btn.pack(side="left")
                self.status_var = tk.StringVar(value="Idle")
                ttk.Label(btn_frame, textvariable=self.status_var).pack(side="left", padx=10)
                self.global_progress = ttk.Progressbar(btn_frame, mode="determinate", maximum=100, length=200)
                self.global_progress.pack(side="left", padx=10)
                self.cancel_btn = ttk.Button(btn_frame, text="Cancel", command=self.cancel_downloads)
                self.cancel_btn.pack(side="left", padx=6)

            def add_url(self) -> None:
                url = self.url_var.get().strip()
                if not url:
                    return
                item_id = self._add_row("Resolving...", url, "0%", "pending")
                self.url_var.set("")
                threading.Thread(target=self._process_url, args=(item_id, url), daemon=True).start()

            def add_multiple(self) -> None:
                # Allow multi-paste (one URL per line)
                text = tk.simpledialog.askstring("Add multiple", "Paste URLs (one per line):", parent=self)
                if not text:
                    return
                for line in text.splitlines():
                    url = line.strip()
                    if not url:
                        continue
                    item_id = self._add_row("Resolving...", url, "0%", "pending")
                    threading.Thread(target=self._process_url, args=(item_id, url), daemon=True).start()

            def _on_drop(self, event: tk.Event) -> None:
                data = getattr(event, "data", "") or ""
                # tkdnd wraps items in braces and space separates them.
                parts = [p.strip("{}") for p in data.split()]
                for part in parts:
                    url = part.strip()
                    if not url:
                        continue
                    item_id = self._add_row("Resolving...", url, "0%", "pending")
                    threading.Thread(target=self._process_url, args=(item_id, url), daemon=True).start()

            def remove_selected(self) -> None:
                for item in self.tree.selection():
                    self.item_paths.pop(item, None)
                    self.item_progress.pop(item, None)
                    self.tree.delete(item)

            def _add_row(
                self,
                title: str,
                url: str,
                progress: str = "0%",
                status: str = "pending",
            ) -> str:
                item_id = self.tree.insert("", "end", values=(title, url, progress, status, "Show", "Retry"))
                self.item_paths[item_id] = None
                self.item_progress[item_id] = float(progress.strip("%")) if "%" in progress else 0.0
                return item_id

            def _process_url(self, item_id: str, url: str) -> None:
                opts = {
                    "quiet": True,
                    "skip_download": True,
                    "extract_flat": True,
                    "noplaylist": False,
                }
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        info = ydl.extract_info(url, download=False)
                except Exception:
                    # Fallback: keep URL as title
                    self._update_row(item_id, title=url)
                    return

                if info is None:
                    self._update_row(item_id, title=url)
                    return

                if info.get("_type") == "playlist" and info.get("entries"):
                    # Remove placeholder row and add each entry.
                    def remove_placeholder() -> None:
                        self.tree.delete(item_id)
                        self.item_paths.pop(item_id, None)
                        self.item_progress.pop(item_id, None)

                    self.after(0, remove_placeholder)
                    for entry in info.get("entries", []):
                        entry_url = entry.get("url") or entry.get("id")
                        if not entry_url:
                            continue
                        if not entry_url.startswith("http"):
                            entry_url = f"https://www.youtube.com/watch?v={entry_url}"
                        title = entry.get("title") or entry_url
                        self.after(0, lambda t=title, u=entry_url: self._add_row(t, u))
                else:
                    title = info.get("title") or url
                    self._update_row(item_id, title=title)

            def choose_folder(self) -> None:
                chosen = filedialog.askdirectory(initialdir=self.download_dir)
                if chosen:
                    self.download_dir = Path(chosen)
                    self.folder_label.config(text=str(self.download_dir))

            def start_downloads(self) -> None:
                item_ids = list(self.tree.get_children())
                if not item_ids:
                    messagebox.showinfo("No URLs", "Add at least one YouTube link.")
                    return

                # Immediate feedback
                for item_id in item_ids:
                    self._update_row(item_id, progress="0%", status="queued")
                self.status_var.set("Starting downloads...")
                self.global_progress["value"] = 0
                self.cancelled = False
                self.download_btn.config(state="disabled")
                threading.Thread(target=self._run_downloads, args=(item_ids,), daemon=True).start()

            def _run_downloads(self, item_ids: List[str]) -> None:
                archive_file = self.download_dir / "downloaded.txt"
                self.status_var.set("Downloading...")

                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    futures = []
                    for item_id in item_ids:
                        url = self.tree.item(item_id, "values")[1]
                        futures.append(executor.submit(self._download_item, item_id, url, archive_file))
                    for future in futures:
                        future.result()

                self.after(0, lambda: self.download_btn.config(state="normal"))
                self.status_var.set("Done")
                self._notify_complete()

            def cancel_downloads(self) -> None:
                self.cancelled = True
                self.status_var.set("Cancelling...")
                self.download_btn.config(state="normal")

            def _download_item(self, item_id: str, url: str, archive_file: Path) -> None:
                def progress_hook(d: dict) -> None:
                    status = d.get("status")
                    if self.cancelled:
                        raise yt_dlp.utils.DownloadCancelled("User cancelled")
                    if status == "downloading":
                        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 1
                        downloaded = d.get("downloaded_bytes", 0)
                        percent = downloaded / total
                        self._update_row(item_id, progress=f"{percent*100:.1f}%", status="downloading")
                    elif status == "finished":
                        self._update_row(item_id, progress="100%", status="postprocessing")
                    elif status == "error":
                        self._update_row(item_id, progress="0%", status="error")

                existing_path = find_existing_path(url, self.download_dir)
                if existing_path:
                    self._set_item_path(item_id, existing_path)
                    self._update_row(item_id, progress="100%", status="skipped (exists)")
                    return
                if self.cancelled:
                    self._update_row(item_id, progress="0%", status="cancelled")
                    return

                ydl_opts = {
                    "format": "bestaudio/best",
                    "outtmpl": str(self.download_dir / "%(id)s - %(title)s.%(ext)s"),
                    "noplaylist": True,
                    "quiet": False,
                    "overwrites": False,
                    "download_archive": str(archive_file),
                    "progress_hooks": [progress_hook],
                    "postprocessors": [
                        {
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3",
                            "preferredquality": "192",
                        }
                    ],
                }

                self.download_dir.mkdir(parents=True, exist_ok=True)

                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                        filename = ydl.prepare_filename(info)
                        final_path = Path(filename).with_suffix(".mp3")
                    self._set_item_path(item_id, final_path)
                    self._update_row(item_id, progress="100%", status="done")
                except Exception as exc:  # noqa: BLE001
                    self._update_row(item_id, progress="0%", status=f"error: {exc}")

            def _update_row(
                self,
                item_id: str,
                title: Optional[str] = None,
                progress: Optional[str] = None,
                status: Optional[str] = None,
            ) -> None:
                def update() -> None:
                    current = list(self.tree.item(item_id, "values"))
                    if title is not None:
                        current[0] = title
                    if progress is not None:
                        current[2] = progress
                        try:
                            self.item_progress[item_id] = float(progress.strip("%"))
                        except Exception:
                            self.item_progress[item_id] = 0.0
                    if status is not None:
                        current[3] = status
                        if status.startswith("error"):
                            current[5] = "Retry"
                    self.tree.item(item_id, values=current)
                    self._update_global_progress()

                self.after(0, update)

            def _set_item_path(self, item_id: str, path: Path) -> None:
                self.item_paths[item_id] = path

            def _handle_click(self, event: tk.Event) -> None:
                region = self.tree.identify("region", event.x, event.y)
                if region != "cell":
                    return
                item_id = self.tree.identify_row(event.y)
                column = self.tree.identify_column(event.x)
                if column == "#5" and item_id:
                    self.show_in_folder(item_id)
                if column == "#6" and item_id:
                    self._retry_item(item_id)

            def _retry_item(self, item_id: str) -> None:
                # Re-run a single failed/skipped download
                url = self.tree.item(item_id, "values")[1]
                self._update_row(item_id, progress="0%", status="queued")
                threading.Thread(
                    target=self._download_item,
                    args=(item_id, url, self.download_dir / "downloaded.txt"),
                    daemon=True,
                ).start()

            def show_in_folder(self, item_id: Optional[str] = None) -> None:
                if item_id is None:
                    selection = self.tree.selection()
                    if not selection:
                        return
                    item_id = selection[0]

                path = self.item_paths.get(item_id)
                if not path or not path.exists():
                    url = self.tree.item(item_id, "values")[1]
                    path = find_existing_path(url, self.download_dir)
                    if not path:
                        messagebox.showinfo("Not found", "File not found on disk yet.")
                        return
                    self._set_item_path(item_id, path)

                self._reveal_file(path)

            def _reveal_file(self, path: Path) -> None:
                try:
                    if sys.platform.startswith("darwin"):
                        subprocess.run(["open", "-R", str(path)], check=False)
                    elif sys.platform.startswith("win"):
                        subprocess.run(["explorer", "/select,", str(path)], check=False)
                    else:
                        subprocess.run(["xdg-open", str(path.parent)], check=False)
                except Exception:
                    messagebox.showinfo("Open failed", f"Could not open folder for {path}")

            def _update_global_progress(self) -> None:
                if not self.item_progress:
                    self.global_progress["value"] = 0
                    return
                avg = sum(self.item_progress.values()) / len(self.item_progress)
                self.global_progress["value"] = avg

            def _notify_complete(self) -> None:
                # Try a lightweight notification; fallback to status label
                try:
                    if sys.platform.startswith("darwin"):
                        subprocess.run(
                            ["osascript", "-e", 'display notification "All downloads finished" with title "Downloader"'],
                            check=False,
                        )
                    elif sys.platform.startswith("win"):
                        script = (
                            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
                            "ContentType = WindowsRuntime];"
                            "$template = Windows.UI.Notifications.ToastNotificationManager::GetTemplateContent("
                            "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
                            "$template.GetElementsByTagName('text').Item(0).AppendChild($template.CreateTextNode('Downloader')) | Out-Null;"
                            "$template.GetElementsByTagName('text').Item(1).AppendChild($template.CreateTextNode('All downloads finished')) | Out-Null;"
                            "$toast = [Windows.UI.Notifications.ToastNotification]::new($template);"
                            "$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Downloader');"
                            "$notifier.Show($toast);"
                        )
                        subprocess.run(["powershell", "-Command", script], check=False)
                    else:
                        subprocess.run(["notify-send", "Downloader", "All downloads finished"], check=False)
                except Exception:
                    pass

        app = DownloadApp()
        app.mainloop()

    launch_gui()
