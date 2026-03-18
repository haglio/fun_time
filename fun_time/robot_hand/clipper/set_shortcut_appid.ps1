param(
    [string]$ShortcutPath = 'fun_time\robot_hand\clipper\Clipper.lnk',
    [string]$AppId = 'FunTime.Clipper'
)

$ErrorActionPreference = 'Stop'

$code = @'
using System;
using System.Runtime.InteropServices;
using System.Text;

[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("000214F9-0000-0000-C000-000000000046")]
interface IShellLinkW
{
    void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszFile, int cch, IntPtr pfd, uint fFlags);
    void GetIDList(out IntPtr ppidl);
    void SetIDList(IntPtr pidl);
    void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszName, int cch);
    void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);
    void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszDir, int cch);
    void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
    void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszArgs, int cch);
    void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
    void GetHotkey(out short pwHotkey);
    void SetHotkey(short wHotkey);
    void GetShowCmd(out int piShowCmd);
    void SetShowCmd(int iShowCmd);
    void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszIconPath, int cch, out int piIcon);
    void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
    void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, uint dwReserved);
    void Resolve(IntPtr hwnd, uint fFlags);
    void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
}

[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")]
interface IPropertyStore
{
    uint GetCount(out uint cProps);
    uint GetAt(uint iProp, out PROPERTYKEY pkey);
    uint GetValue(ref PROPERTYKEY key, out PROPVARIANT pv);
    uint SetValue(ref PROPERTYKEY key, ref PROPVARIANT propvar);
    uint Commit();
}

[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("0000010B-0000-0000-C000-000000000046")]
interface IPersistFile
{
    void GetClassID(out Guid pClassID);
    void IsDirty();
    void Load([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, uint dwMode);
    void Save([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, bool fRemember);
    void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string pszFileName);
    void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string ppszFileName);
}

[StructLayout(LayoutKind.Sequential, Pack = 4)]
struct PROPERTYKEY
{
    public Guid fmtid;
    public uint pid;
}

[StructLayout(LayoutKind.Explicit)]
struct PROPVARIANT
{
    [FieldOffset(0)] public ushort vt;
    [FieldOffset(8)] public IntPtr pwszVal;

    public static PROPVARIANT FromString(string value)
    {
        var pv = new PROPVARIANT();
        pv.vt = 31;
        pv.pwszVal = Marshal.StringToCoTaskMemUni(value);
        return pv;
    }

    public void Clear()
    {
        if (pwszVal != IntPtr.Zero)
        {
            Marshal.FreeCoTaskMem(pwszVal);
            pwszVal = IntPtr.Zero;
        }
    }
}

[ComImport, Guid("00021401-0000-0000-C000-000000000046")]
class ShellLink
{
}

public static class LnkAppIdSetter
{
    public static void SetAppId(string lnkPath, string appId)
    {
        var link = (IShellLinkW)new ShellLink();
        var persist = (IPersistFile)link;
        persist.Load(lnkPath, 0x00000002);

        var store = (IPropertyStore)link;
        var key = new PROPERTYKEY
        {
            fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"),
            pid = 5
        };
        var pv = PROPVARIANT.FromString(appId);
        try
        {
            uint hr = store.SetValue(ref key, ref pv);
            if (hr != 0)
            {
                throw new COMException("SetValue failed", (int)hr);
            }
            hr = store.Commit();
            if (hr != 0)
            {
                throw new COMException("Commit failed", (int)hr);
            }
            persist.Save(lnkPath, true);
        }
        finally
        {
            pv.Clear();
        }
    }
}
'@

if (-not ('LnkAppIdSetter' -as [type])) {
    Add-Type -TypeDefinition $code -Language CSharp
}

$lnk = Join-Path (Get-Location).Path $ShortcutPath
[LnkAppIdSetter]::SetAppId($lnk, $AppId)
Write-Output "Set AppUserModelID=$AppId on $lnk"
