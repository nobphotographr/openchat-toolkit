// Standalone Windows/.NET UIA + Win32 navigation. No Computer Use dependency.
// Room names are checked against collector-config.json. Navigation never types
// into a message composer or sends messages. Screen readers preserve clipboard
// formats in memory; there is no posting module in this executable.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Automation;

public partial class LineWindowPrep {
    public static string Room;
    static string stage = "initialize";
    static readonly List<string> actions = new List<string>();
    static readonly string exe = Path.GetFullPath(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "LINE/bin/current/LINE.exe"));
    delegate bool EnumProc(IntPtr hwnd, IntPtr arg);
    [StructLayout(LayoutKind.Sequential)] struct RECT { public int left,top,right,bottom; }
    [StructLayout(LayoutKind.Sequential)] struct POINT { public int x,y; public POINT(int x,int y){this.x=x;this.y=y;} }
    [StructLayout(LayoutKind.Sequential)] struct MONITORINFO {public int size; public RECT monitor,work; public uint flags;}
    [StructLayout(LayoutKind.Sequential)] struct MOUSEINPUT {public int dx,dy;public uint mouseData,flags,time;public UIntPtr extra;}
    [StructLayout(LayoutKind.Explicit)] struct INPUTUNION {[FieldOffset(0)] public MOUSEINPUT mouse;}
    [StructLayout(LayoutKind.Sequential)] struct INPUT {public uint type;public INPUTUNION value;}
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc fn,IntPtr arg);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h,StringBuilder text,int count);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h,out uint pid);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h,out RECT r);
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h,int cmd);
    [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr h,IntPtr after,int x,int y,int width,int height,uint flags);
    [DllImport("user32.dll")] static extern IntPtr MonitorFromPoint(POINT p,uint flags);
    [DllImport("user32.dll")] static extern bool GetMonitorInfo(IntPtr h,ref MONITORINFO info);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] static extern bool BringWindowToTop(IntPtr h);
    [DllImport("user32.dll")] static extern IntPtr SetActiveWindow(IntPtr h);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] static extern bool AttachThreadInput(uint from,uint to,bool attach);
    [DllImport("user32.dll")] static extern bool SetCursorPos(int x,int y);
    [DllImport("user32.dll")] static extern IntPtr WindowFromPoint(POINT p);
    [DllImport("user32.dll")] static extern IntPtr GetAncestor(IntPtr h,uint flags);
    [DllImport("user32.dll")] static extern uint SendInput(uint count,INPUT[] inputs,int size);
    [DllImport("user32.dll")] static extern IntPtr OpenInputDesktop(uint flags,bool inherit,uint access);
    [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr desktop);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern bool GetUserObjectInformation(IntPtr obj,int index,StringBuilder data,int size,out int needed);
    [DllImport("wtsapi32.dll",CharSet=CharSet.Unicode)] static extern bool WTSQuerySessionInformation(IntPtr server,int session,int info,out IntPtr data,out int size);
    [DllImport("wtsapi32.dll")] static extern void WTSFreeMemory(IntPtr data);
    [DllImport("user32.dll")] static extern bool PostMessage(IntPtr hwnd,uint message,IntPtr w,IntPtr l);
    [DllImport("user32.dll",CharSet=CharSet.Unicode,EntryPoint="SendMessageTimeoutW")]
    static extern IntPtr CaptionHitTest(IntPtr hwnd,uint message,IntPtr w,IntPtr l,uint flags,uint timeout,out IntPtr result);

    static void Require(bool yes,string reason){if(!yes)throw new InvalidOperationException(reason);}
    static string Title(IntPtr h){var text=new StringBuilder(2048);GetWindowText(h,text,text.Capacity);return text.ToString();}
    static void Session(){
        IntPtr data;int size;
        Require(WTSQuerySessionInformation(IntPtr.Zero,Process.GetCurrentProcess().SessionId,8,out data,out size),"session_state_unavailable");
        try{Require(size>=4&&Marshal.ReadInt32(data)==0,"session_not_active");}finally{WTSFreeMemory(data);}
        var desktop=OpenInputDesktop(0,false,1);Require(desktop!=IntPtr.Zero,"input_desktop_unavailable");
        try{var name=new StringBuilder(256);int needed;Require(GetUserObjectInformation(desktop,2,name,512,out needed)&&name.ToString()=="Default","secure_or_other_desktop");}
        finally{CloseDesktop(desktop);}
    }
    static bool Identity(IntPtr hwnd,string title){
        if(Title(hwnd)!=title||Class(hwnd)!="Qt663QWindowIcon")return false;
        uint pid;GetWindowThreadProcessId(hwnd,out pid);
        try {var p=Process.GetProcessById((int)pid);return p.SessionId==Process.GetCurrentProcess().SessionId&&String.Equals(p.MainModule.FileName,exe,StringComparison.OrdinalIgnoreCase);}
        catch{return false;}
    }
    static IntPtr Find(string title){
        var found=new List<IntPtr>();
        for(int attempt=0;attempt<8;attempt++){
            found.Clear();
            EnumWindows((h,a)=>{if(Identity(h,title)&&Class(h)=="Qt663QWindowIcon")found.Add(h);return true;},IntPtr.Zero);
            if(found.Count<=1)break;
            // Qt can briefly retain the preceding dialog during window teardown.
            // Wait for uniqueness; never pick an arbitrary same-named window.
            Thread.Sleep(150);Session();
        }
        if(found.Count>1)Console.Error.WriteLine(new JavaScriptSerializer().Serialize(new{finding=title,windows=found.Select(h=>new{hwnd=h.ToInt64(),cls=Class(h),visible=IsWindowVisible(h),title=Title(h)}).ToArray()}));
        Require(found.Count<=1,"exact_window_not_unique");return found.Count==1?found[0]:IntPtr.Zero;
    }
    static void Guard(IntPtr hwnd,string title,bool foreground){
        Session();Require(Identity(hwnd,title),"window_identity_changed");
        if(foreground)Require(GetForegroundWindow()==hwnd,"foreground_changed");
    }
    static void Restore(IntPtr hwnd,string title){
        Guard(hwnd,title,false);
        Require(IsWindowEnabled(hwnd),"window_dialog_preserved");
        var info=new MONITORINFO();info.size=Marshal.SizeOf(info);
        Require(GetMonitorInfo(MonitorFromPoint(new POINT(0,0),1),ref info),"monitor_unavailable");
        int width=Math.Min(1000,info.work.right-info.work.left-48),height=Math.Min(title==Room?1600:1000,info.work.bottom-info.work.top-48);
        Require(width>=400&&height>=400,"work_area_too_small");
        ShowWindow(hwnd,9);
        Require(SetWindowPos(hwnd,IntPtr.Zero,info.work.left+24,info.work.top+24,width,height,0x0040),"window_move_failed");
        SetForegroundWindow(hwnd);
        Thread.Sleep(150);
        if(GetForegroundWindow()!=hwnd){
            // Temporary input-thread linkage, always detached. No OS settings change.
            uint pid;uint foreground=GetWindowThreadProcessId(GetForegroundWindow(),out pid);
            uint current=GetCurrentThreadId(),target=GetWindowThreadProcessId(hwnd,out pid);bool attached=false,targetAttached=false;
            try{
                if(foreground!=0&&foreground!=current)attached=AttachThreadInput(current,foreground,true);
                if(target!=0&&target!=current&&target!=foreground)targetAttached=AttachThreadInput(current,target,true);
                Guard(hwnd,title,false);Require(IsWindowEnabled(hwnd),"window_dialog_preserved");
                BringWindowToTop(hwnd);SetActiveWindow(hwnd);SetForegroundWindow(hwnd);
                for(int wait=0;wait<10&&GetForegroundWindow()!=hwnd;wait++)Thread.Sleep(50);
            }
            finally{
                if(targetAttached)AttachThreadInput(current,target,false);
                if(attached)AttachThreadInput(current,foreground,false);
            }
            Thread.Sleep(150);
        }
        // Background scheduled processes may be denied foreground activation.
        // Only click an unobscured, verified caption on the exact LINE window;
        // never click an arbitrary coordinate or change Windows security settings.
        if(GetForegroundWindow()!=hwnd)FocusVerifiedCaption(hwnd,title);
        Guard(hwnd,title,true);Require(IsWindowVisible(hwnd)&&!IsIconic(hwnd),"window_not_restored");
        actions.Add("restore_and_foreground:"+title);
    }
    static void FocusVerifiedCaption(IntPtr hwnd,string title){
        Guard(hwnd,title,false);
        Require(IsWindowEnabled(hwnd)&&IsWindowVisible(hwnd)&&!IsIconic(hwnd),"window_not_focusable");
        RECT r;Require(GetWindowRect(hwnd,out r),"window_bounds_missing");
        var point=new POINT((r.left+r.right)/2,r.top+16);
        Require(GetAncestor(WindowFromPoint(point),2)==hwnd,"caption_obscured");
        IntPtr hit;
        Require(CaptionHitTest(hwnd,0x0084,IntPtr.Zero,new IntPtr((point.y<<16)|(point.x&65535)),2,2000,out hit)!=IntPtr.Zero&&hit.ToInt32()==2,"caption_not_verified");
        Require(SetCursorPos(point.x,point.y),"pointer_failed");Guard(hwnd,title,false);
        Require(IsWindowEnabled(hwnd)&&GetAncestor(WindowFromPoint(point),2)==hwnd,"caption_changed");
        var clicks=new[]{new INPUT{type=0,value=new INPUTUNION{mouse=new MOUSEINPUT{flags=2}}},new INPUT{type=0,value=new INPUTUNION{mouse=new MOUSEINPUT{flags=4}}}};
        Require(SendInput(2,clicks,Marshal.SizeOf(typeof(INPUT)))==2,"caption_focus_unknown");
        for(int wait=0;wait<10&&GetForegroundWindow()!=hwnd;wait++)Thread.Sleep(50);
        Guard(hwnd,title,true);actions.Add("verified_caption_focus:"+title);
    }
    static AutomationElement Root(IntPtr hwnd,string title){
        Guard(hwnd,title,false);var root=AutomationElement.FromHandle(hwnd);Require(root.Current.Name==title,"uia_title_mismatch");return root;
    }
    static List<AutomationElement> Elements(AutomationElement root,ControlType type){
        var all=root.FindAll(TreeScope.Descendants,new PropertyCondition(AutomationElement.ControlTypeProperty,type));
        Require(all.Count<1000,"uia_tree_unexpected");
        return all.Cast<AutomationElement>().Where(e=>!e.Current.IsOffscreen&&e.Current.IsEnabled&&!e.Current.BoundingRectangle.IsEmpty).ToList();
    }
    static string Value(AutomationElement e){object pattern;Require(e.TryGetCurrentPattern(ValuePattern.Pattern,out pattern),"value_pattern_missing");return ((ValuePattern)pattern).Current.Value;}
    static AutomationElement Search(IntPtr hwnd){
        var root=Root(hwnd,"LINE");var bounds=root.Current.BoundingRectangle;
        var candidates=Elements(root,ControlType.Edit).Where(e=>{
            var b=e.Current.BoundingRectangle;
            return b.Top>=bounds.Top&&b.Bottom<bounds.Top+bounds.Height*0.22&&b.Width>bounds.Width*0.30;
        }).ToList();
        Require(candidates.Count==1,"main_search_not_unique");return candidates[0];
    }
    static List<AutomationElement> Rows(IntPtr hwnd){
        var root=Root(hwnd,"LINE");var lists=Elements(root,ControlType.List);
        Require(lists.Count==1,"search_list_not_unique");return Elements(lists[0],ControlType.ListItem);
    }
    static void RequireDraftIsEmpty(IntPtr hwnd){
        // Read-only assertion: Value reads ValuePattern.Current.Value. This
        // function never sets, clears, types into, or otherwise changes a draft.
        var edits=Elements(Root(hwnd,Room),ControlType.Edit);Require(edits.Count==1,"room_input_not_unique");Require(String.IsNullOrEmpty(Value(edits[0])),"existing_draft_preserved");
    }
    static object InspectWindow(IntPtr hwnd,string title){
        if(hwnd==IntPtr.Zero)return null;
        // Qt can omit its UIA title while minimized. Report metadata without
        // restoring the window; --inspect must remain read-only.
        if(IsIconic(hwnd)||!IsWindowVisible(hwnd))return new {window_id=hwnd.ToInt64(),title=title,
            visible=IsWindowVisible(hwnd),minimized=IsIconic(hwnd),uia="not_read_while_hidden"};
        var root=Root(hwnd,title);var edits=Elements(root,ControlType.Edit);
        return new {window_id=hwnd.ToInt64(),title=title,visible=IsWindowVisible(hwnd),minimized=IsIconic(hwnd),foreground=GetForegroundWindow()==hwnd,
            edits=edits.Select(e=>new {bounds=e.Current.BoundingRectangle.ToString(),value_empty=String.IsNullOrEmpty(Value(e)),search_matches=Value(e)==Room}).ToArray(),
            list_count=Elements(root,ControlType.List).Count,list_item_count=Elements(root,ControlType.ListItem).Count};
    }
    static void DoubleClick(IntPtr hwnd,AutomationElement row){
        Guard(hwnd,"LINE",true);Require(Value(Search(hwnd))==Room,"search_changed");
        var r=row.Current.BoundingRectangle;var root=Root(hwnd,"LINE").Current.BoundingRectangle;
        Require(root.Contains(r)&&r.Width>20&&r.Height>5,"row_bounds_invalid");
        var point=new POINT((int)(r.Left+r.Width/2),(int)(r.Top+r.Height/2));
        Require(GetAncestor(WindowFromPoint(point),2)==hwnd,"row_obscured_by_other_window");
        Require(SetCursorPos(point.x,point.y),"cursor_move_failed");
        Guard(hwnd,"LINE",true);
        var input=new[]{new INPUT{type=0,value=new INPUTUNION{mouse=new MOUSEINPUT{flags=2}}},new INPUT{type=0,value=new INPUTUNION{mouse=new MOUSEINPUT{flags=4}}}};
        for(int i=0;i<2;i++){
            Require(GetAncestor(WindowFromPoint(point),2)==hwnd,"row_context_changed");
            Require(SendInput(2,input,Marshal.SizeOf(typeof(INPUT)))==2,"mouse_input_incomplete");
            if(i==0)Thread.Sleep(70);
        }
    }
    static IntPtr Open(){
        var room=Find(Room);
        if(room!=IntPtr.Zero){Restore(room,Room);RequireDraftIsEmpty(room);actions.Add("reuse_exact_room");return room;}
        var main=Find("LINE");
        if(main==IntPtr.Zero){
            Require(File.Exists(exe),"line_executable_missing");
            Process.Start(new ProcessStartInfo(exe){UseShellExecute=true});actions.Add("launch_line");
            for(int wait=0;wait<50&&main==IntPtr.Zero;wait++){Thread.Sleep(200);Session();main=Find("LINE");}
        }
        Require(main!=IntPtr.Zero,"line_main_unavailable_login_required");
        Restore(main,"LINE");stage="search";
        var edit=Search(main);Guard(main,"LINE",true);edit.SetFocus();
        Require(Automation.Compare(AutomationElement.FocusedElement,edit),"search_focus_not_confirmed");
        object pattern;Require(edit.TryGetCurrentPattern(ValuePattern.Pattern,out pattern),"search_not_writable");
        Guard(main,"LINE",true);Require(Automation.Compare(Search(main),edit),"search_element_changed");
        ((ValuePattern)pattern).SetValue(Room);actions.Add("set_search_full_test_room_name");
        Thread.Sleep(600);Require(Value(Search(main))==Room,"search_value_not_confirmed");
        var count=Rows(main).Count;Require(count>0&&count<=4,"search_results_not_bounded");
        stage="open_search_result";
        for(int i=0;i<count;i++){
            Restore(main,"LINE");Require(Value(Search(main))==Room,"search_changed");
            var rows=Rows(main);Require(rows.Count==count,"search_result_count_changed");
            DoubleClick(main,rows[i]);actions.Add("open_search_row:"+i);
            for(int wait=0;wait<12;wait++){Thread.Sleep(100);Session();room=Find(Room);if(room!=IntPtr.Zero)break;}
            if(room!=IntPtr.Zero){Restore(room,Room);RequireDraftIsEmpty(room);actions.Add("exact_room_title_verified");return room;}
        }
        throw new InvalidOperationException("exact_test_room_did_not_open");
    }
}
