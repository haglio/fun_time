Set sh = CreateObject("WScript.Shell")

gitbash = "C:\Program Files\Git\bin\bash.exe"
cmd = """" & gitbash & """" & " -lc " & """" & "cd /c/path/to/suite-root && ./vlc_wall.sh" & """"

' 0 = hidden window, False = do not wait (AHK remains the controller)
sh.Run cmd, 0, False
