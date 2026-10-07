"""A small Win32 target window for manual OCR and input smoke tests."""

import tkinter as tk

root = tk.Tk()
root.title("AutoFlow 测试窗口")
root.geometry("500x280")
label = tk.Label(root, text="开始 START", font=("Microsoft YaHei UI", 24))
label.pack(pady=40)


def clicked():
    label.configure(text="完成 DONE")


tk.Button(root, text="点击 CLICK", command=clicked, font=("Microsoft YaHei UI", 16)).pack()
root.mainloop()
