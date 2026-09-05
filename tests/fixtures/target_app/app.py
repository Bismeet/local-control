"""Deterministic Tkinter target application for local-control testing.

Specified by TEST_PLAN section 2.3 and IMPLEMENTATION_PLAN section 12.
"""

import argparse
import json
import socket
import threading
import tkinter as tk
from tkinter import messagebox, ttk


class TargetApp:
    """LC Test Target Tkinter Application."""

    def __init__(self, root: tk.Tk, port: int = 0, port_file: str | None = None) -> None:
        self.root = root
        self.root.title("LC Test Target")
        self.root.geometry("800x600+100+100")
        self.root.resizable(False, False)

        self.count = 0
        self.buttons: dict[str, ttk.Button] = {}

        self._build_ui()
        self._start_command_server(port, port_file)

    def _build_ui(self) -> None:
        # Menu Bar
        menubar = tk.Menu(self.root)
        filemenu = tk.Menu(menubar, tearoff=0)
        filemenu.add_command(label="New")
        filemenu.add_separator()
        filemenu.add_command(label="Exit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=filemenu)

        helpmenu = tk.Menu(menubar, tearoff=0)
        helpmenu.add_command(
            label="About",
            command=lambda: messagebox.showinfo("About", "LC Test Target v1.0"),
        )
        menubar.add_cascade(label="Help", menu=helpmenu)
        self.root.config(menu=menubar)

        # Counter Label
        self.counter_var = tk.StringVar(value="Count: 0")
        self.counter_label = ttk.Label(
            self.root, textvariable=self.counter_var, font=("Helvetica", 14, "bold")
        )
        self.counter_label.pack(pady=10)

        # Buttons Frame
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(pady=10)

        self.btn_alpha = ttk.Button(btn_frame, text="Alpha", command=self._on_alpha)
        self.btn_alpha.grid(row=0, column=0, padx=10)
        self.buttons["Alpha"] = self.btn_alpha

        self.btn_beta = ttk.Button(btn_frame, text="Beta", command=self._on_beta)
        self.btn_beta.grid(row=0, column=1, padx=10)
        self.buttons["Beta"] = self.btn_beta

        self.btn_delete = ttk.Button(btn_frame, text="Delete", command=self._on_delete)
        self.btn_delete.grid(row=0, column=2, padx=10)
        self.buttons["Delete"] = self.btn_delete

        # Inputs Frame
        inputs_frame = ttk.Frame(self.root)
        inputs_frame.pack(pady=10, fill="x", padx=40)

        ttk.Label(inputs_frame, text="Main Entry:").grid(row=0, column=0, sticky="w", pady=5)
        self.entry_main = ttk.Entry(inputs_frame, width=40)
        self.entry_main.grid(row=0, column=1, sticky="w", padx=10, pady=5)

        ttk.Label(inputs_frame, text="Password Entry:").grid(row=1, column=0, sticky="w", pady=5)
        self.entry_secret = ttk.Entry(inputs_frame, width=40, show="*")
        self.entry_secret.grid(row=1, column=1, sticky="w", padx=10, pady=5)

        # Multiline Text Widget
        text_frame = ttk.Frame(self.root)
        text_frame.pack(pady=10, fill="both", expand=True, padx=40)

        self.text_widget = tk.Text(text_frame, height=5, width=60)
        self.text_widget.pack(side="left", fill="both", expand=True)

        # Listbox with 10 items
        list_frame = ttk.Frame(self.root)
        list_frame.pack(pady=10, padx=40, fill="x")

        ttk.Label(list_frame, text="Listbox:").pack(anchor="w")
        self.listbox = tk.Listbox(list_frame, height=5)
        for i in range(1, 11):
            self.listbox.insert(tk.END, f"Item {i}")
        self.listbox.pack(fill="x")

        # Slider
        slider_frame = ttk.Frame(self.root)
        slider_frame.pack(pady=10, padx=40, fill="x")
        ttk.Label(slider_frame, text="Slider:").pack(anchor="w")
        self.slider = ttk.Scale(slider_frame, from_=0, to=100, orient="horizontal")
        self.slider.set(0)
        self.slider.pack(fill="x")

    def _on_alpha(self) -> None:
        self.count += 1
        self.counter_var.set(f"Count: {self.count}")

    def _on_beta(self) -> None:
        self.counter_var.set(f"Count: Beta clicked ({self.count})")

    def _on_delete(self) -> None:
        messagebox.askyesno("Confirm Delete", "Are you sure you want to delete?")

    def _start_command_server(self, port: int, port_file: str | None) -> None:
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.bind(("127.0.0.1", port))
        self.server_sock.listen(1)
        actual_port = self.server_sock.getsockname()[1]
        self.port = actual_port

        if port_file:
            with open(port_file, "w", encoding="utf-8") as f:
                f.write(str(actual_port))

        t = threading.Thread(target=self._command_loop, daemon=True)
        t.start()

    def _command_loop(self) -> None:
        while True:
            try:
                conn, _ = self.server_sock.accept()
                with conn:
                    data = conn.recv(4096).decode("utf-8").strip()
                    if not data:
                        continue
                    response = self._handle_command(data)
                    conn.sendall(json.dumps(response).encode("utf-8") + b"\n")
            except Exception:
                break

    def _handle_command(self, cmd: str) -> dict:
        parts = cmd.split()
        op = parts[0] if parts else ""

        if op == "read_state":
            return {
                "count": self.count,
                "main_text": self.entry_main.get(),
                "secret_text": self.entry_secret.get(),
                "multiline": self.text_widget.get("1.0", tk.END).strip(),
                "slider": self.slider.get(),
                "x": self.root.winfo_x(),
                "y": self.root.winfo_y(),
                "width": self.root.winfo_width(),
                "height": self.root.winfo_height(),
            }
        elif op == "disable_button" and len(parts) > 1:
            btn_name = parts[1]
            if btn_name in self.buttons:
                self.root.after(0, lambda: self.buttons[btn_name].config(state="disabled"))
                return {"status": "ok", "button": btn_name, "state": "disabled"}
            return {"status": "error", "message": f"Button {btn_name} not found"}
        elif op == "enable_button" and len(parts) > 1:
            btn_name = parts[1]
            if btn_name in self.buttons:
                self.root.after(0, lambda: self.buttons[btn_name].config(state="normal"))
                return {"status": "ok", "button": btn_name, "state": "normal"}
            return {"status": "error", "message": f"Button {btn_name} not found"}
        elif op == "rename_button" and len(parts) > 2:
            old_name, new_name = parts[1], parts[2]
            if old_name in self.buttons:
                btn = self.buttons.pop(old_name)
                self.buttons[new_name] = btn
                self.root.after(0, lambda: btn.config(text=new_name))
                return {"status": "ok", "old": old_name, "new": new_name}
            return {"status": "error", "message": f"Button {old_name} not found"}
        elif op == "move_window" and len(parts) > 2:
            x, y = parts[1], parts[2]
            self.root.after(0, lambda: self.root.geometry(f"+{x}+{y}"))
            return {"status": "ok", "x": x, "y": y}
        elif op == "quit":
            self.root.after(100, self.root.quit)
            return {"status": "quitting"}

        return {"status": "unknown_command", "command": cmd}


def main() -> None:
    parser = argparse.ArgumentParser(description="LC Test Target App")
    parser.add_argument("--port", type=int, default=0, help="TCP command port (0 for dynamic)")
    parser.add_argument("--port-file", type=str, default=None, help="File to write actual port to")
    args = parser.parse_args()

    try:
        from local_control.observation.screen import (
            ensure_interactive_desktop,
            init_dpi_awareness,
        )

        init_dpi_awareness()
        ensure_interactive_desktop()
    except Exception:
        pass

    root = tk.Tk()
    app = TargetApp(root, port=args.port, port_file=args.port_file)
    root.update()
    print(f"LC Test Target running on port {app.port}", flush=True)
    root.mainloop()


if __name__ == "__main__":
    main()
