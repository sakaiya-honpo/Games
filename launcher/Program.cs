using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;

class Launcher
{
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern int MessageBox(IntPtr hWnd, string text, string caption, uint type);

    [STAThread]
    static void Main()
    {
        try
        {
            string dir = AppDomain.CurrentDomain.BaseDirectory;
            Environment.SetEnvironmentVariable("TCL_LIBRARY",
                Path.Combine(dir, "tcl", "tcl8.6"));
            Environment.SetEnvironmentVariable("TK_LIBRARY",
                Path.Combine(dir, "tcl", "tk8.6"));
            Directory.SetCurrentDirectory(dir);

            Process.Start(new ProcessStartInfo
            {
                FileName         = Path.Combine(dir, "pythonw.exe"),
                Arguments        = $"\"{Path.Combine(dir, "aar_tool.py")}\"",
                WorkingDirectory = dir,
                UseShellExecute  = false,
            });
        }
        catch (Exception ex)
        {
            MessageBox(IntPtr.Zero,
                $"起動に失敗しました:\n{ex.Message}",
                "プレイメモメーカー",
                0x10u); // MB_ICONERROR
        }
    }
}
