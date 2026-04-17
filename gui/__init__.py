import customtkinter as ctk


def labeled_frame(parent, title, **kw):
    """Drop-in replacement for ttk.LabelFrame.

    Returns (outer, inner): pack/grid `outer` on the parent;
    place content widgets inside `inner`.
    """
    outer = ctk.CTkFrame(parent, **kw)
    ctk.CTkLabel(outer, text=f" {title} ",
                 font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=8, pady=(6, 2))
    inner = ctk.CTkFrame(outer, fg_color="transparent")
    inner.pack(fill="both", expand=True)
    return outer, inner
