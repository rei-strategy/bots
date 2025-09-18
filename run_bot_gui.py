#!/usr/bin/env python3
import os
import sys
import threading
import queue
import subprocess
import tkinter as tk
from tkinter import scrolledtext, messagebox
from tkinter import ttk

class LeadBotGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("⚡ Lead Bot Controller")
        self.geometry("500x500")
        self.resizable(False, False)
        self._build_ui()
        self.log_queue = queue.Queue()

    def _build_ui(self):
        # Title
        title = ttk.Label(self, text="Lead Bot Interface", font=("Helvetica", 18, "bold"))
        title.pack(pady=(15,5))

        # Instructions
        instr = ttk.Label(self, text="Select one or more sources and click Run", font=("Helvetica", 10))
        instr.pack(pady=(0,15))

        # Sources frame
        frm = ttk.Frame(self)
        frm.pack(pady=5)
        self.var_rl = tk.BooleanVar(value=True)
        self.var_bs = tk.BooleanVar(value=False)
        chk1 = ttk.Checkbutton(frm, text="Reuben Lublin", variable=self.var_rl)
        chk2 = ttk.Checkbutton(frm, text="Brock & Scott", variable=self.var_bs)
        chk1.grid(row=0, column=0, sticky="w", padx=10, pady=5)
        chk2.grid(row=1, column=0, sticky="w", padx=10, pady=5)

        # Run button
        self.btn_run = ttk.Button(self, text="Run Bot", command=self._on_run)
        self.btn_run.pack(pady=10)

        # Progress bar
        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill="x", padx=15, pady=(0,10))

        # Log pane
        self.log = scrolledtext.ScrolledText(self, height=15, state="disabled", wrap="word", font=("Courier", 9))
        self.log.pack(fill="both", expand=True, padx=15, pady=(0,15))

    def _on_run(self):
        sources = []
        if self.var_rl.get():
            sources.append("reuben_lublin")
        if self.var_bs.get():
            sources.append("brock_and_scott")
        if not sources:
            messagebox.showwarning("No source selected", "Please select at least one source.")
            return

        # Disable UI
        self.btn_run.config(state="disabled")
        self.progress.start(10)
        self.log.config(state="normal")
        self.log.delete("1.0", tk.END)
        self.log.config(state="disabled")

        # Start background thread
        threading.Thread(target=self._run_bot, args=(sources,), daemon=True).start()
        self.after(100, self._flush_log)

    def _run_bot(self, sources):
        project_dir = os.getcwd()
        venv_py     = os.path.join(project_dir, ".venv", "bin", "python")
        main_py     = os.path.join(project_dir, "main.py")
        cmd = [venv_py, main_py] + sources

        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
        except Exception as e:
            self.log_queue.put(f"ERROR launching bot: {e}\n")
            self.log_queue.put(None)
            return

        for line in proc.stdout:
            self.log_queue.put(line)
        proc.stdout.close()
        proc.wait()
        self.log_queue.put(f"\nProcess exited with code {proc.returncode}\n")
        self.log_queue.put(None)

    def _flush_log(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                if msg is None:
                    # Done
                    self.progress.stop()
                    self.btn_run.config(state="normal")
                    return
                self.log.config(state="normal")
                self.log.insert(tk.END, msg)
                self.log.see(tk.END)
                self.log.config(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._flush_log)

if __name__ == "__main__":
    app = LeadBotGUI()
    app.mainloop()