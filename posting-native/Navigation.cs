// Standalone Windows/.NET UIA + Win32 navigation. No Computer Use dependency.
// Each session binds one registered room. Navigation modes never type/send a message. Draft and explicit
// test-send sessions are isolated in LineDraftSession.cs. Read-only Copy helpers
// preserve and restore clipboard formats without logging existing contents.
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
    public const bool Production = false;
    static void AllowRoom(string room){
        var path=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"..","collector-config.json");
        var config=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(File.ReadAllText(path,Encoding.UTF8));
        var entries=(System.Collections.IEnumerable)config["rooms"];
        var names=entries.Cast<Dictionary<string,object>>().Select(r=>(string)r["name"]);
        Require(!String.IsNullOrWhiteSpace(room)&&room!="LINE"&&!room.Any(Char.IsControl)&&names.Count(n=>n==room)==1,"room_not_registered");Room=room;
    }
    static void CloseEmptyRoom(){
        var h=Find(Room);if(h==IntPtr.Zero)return;Guard(h,Room,false);
        Require(IsWindowEnabled(h),"room_dialog_preserved");
        Require(!HasPopup(h),"menu_preserved");
        EmptyDraft(h);Guard(h,Room,false);
        Require(PostMessage(h,16,IntPtr.Zero,IntPtr.Zero),"close_failed");
        for(int i=0;i<20;i++){Thread.Sleep(100);if(!Identity(h,Room)||!IsWindowVisible(h))return;}
        throw new InvalidOperationException("close_not_confirmed");
    }
    [DllImport("user32.dll")] static extern bool IsWindowEnabled(IntPtr hwnd);
    static bool HasPopup(IntPtr h){
        uint pid;GetWindowThreadProcessId(h,out pid);bool found=false;
        EnumWindows((w,a)=>{uint other;GetWindowThreadProcessId(w,out other);
            if(other==pid&&IsWindowVisible(w)&&WindowClass(w)=="Qt663QWindowPopupSaveBits")found=true;return true;},IntPtr.Zero);return found;
    }

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
        if(Title(hwnd)!=title)return false;
        uint pid;GetWindowThreadProcessId(hwnd,out pid);
        try {var p=Process.GetProcessById((int)pid);return p.SessionId==Process.GetCurrentProcess().SessionId&&String.Equals(p.MainModule.FileName,exe,StringComparison.OrdinalIgnoreCase);}
        catch{return false;}
    }
    static IntPtr Find(string title){
        var found=new List<IntPtr>();
        for(int attempt=0;attempt<8;attempt++){
            found.Clear();
            EnumWindows((h,a)=>{if(Identity(h,title)&&WindowClass(h)=="Qt663QWindowIcon")found.Add(h);return true;},IntPtr.Zero);
            if(found.Count<=1)break;Thread.Sleep(150);Session();
        }
        Require(found.Count<=1,"exact_window_not_unique");return found.Count==1?found[0]:IntPtr.Zero;
    }
    static void Guard(IntPtr hwnd,string title,bool foreground){
        Session();Require(Identity(hwnd,title),"window_identity_changed");
        if(foreground)Require(GetForegroundWindow()==hwnd,"foreground_changed");
    }
    static void Restore(IntPtr hwnd,string title){
        Guard(hwnd,title,false);
        var info=new MONITORINFO();info.size=Marshal.SizeOf(info);
        Require(GetMonitorInfo(MonitorFromPoint(new POINT(0,0),1),ref info),"monitor_unavailable");
        int width=Math.Min(1000,info.work.right-info.work.left-48),height=Math.Min(title==Room?1600:1000,info.work.bottom-info.work.top-48);
        Require(width>=400&&height>=400,"work_area_too_small");
        ShowWindow(hwnd,9);
        Require(SetWindowPos(hwnd,IntPtr.Zero,info.work.left+24,info.work.top+24,width,height,0x0040),"window_move_failed");
        SetForegroundWindow(hwnd);Thread.Sleep(150);
        if(GetForegroundWindow()!=hwnd){
            // Temporary input-thread linkage, always detached. No OS settings change.
            uint pid;uint foreground=GetWindowThreadProcessId(GetForegroundWindow(),out pid);
            uint current=GetCurrentThreadId();bool attached=false;
            try{if(foreground!=0&&foreground!=current)attached=AttachThreadInput(current,foreground,true);SetForegroundWindow(hwnd);for(int wait=0;wait<10&&GetForegroundWindow()!=hwnd;wait++)Thread.Sleep(50);}
            finally{if(attached)AttachThreadInput(current,foreground,false);}
        }
        if(GetForegroundWindow()!=hwnd&&IsWindowEnabled(hwnd))FocusVerifiedCaption(hwnd,title);
        if(GetForegroundWindow()!=hwnd){uint focusPid;GetWindowThreadProcessId(GetForegroundWindow(),out focusPid);actions.Add("foreground_process:"+Process.GetProcessById((int)focusPid).ProcessName);actions.Add("foreground_class:"+WindowClass(GetForegroundWindow()));}
        Guard(hwnd,title,true);Require(IsWindowVisible(hwnd)&&!IsIconic(hwnd),"window_not_restored");
        actions.Add("restore_and_foreground:"+title);
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
    static void EmptyDraft(IntPtr hwnd){
        Require(IsWindowEnabled(hwnd)&&!HasPopup(hwnd),"room_dialog_or_menu_preserved");
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
        if(room!=IntPtr.Zero){Restore(room,Room);EmptyDraft(room);actions.Add("reuse_exact_room");return room;}
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
            if(room!=IntPtr.Zero){Restore(room,Room);EmptyDraft(room);actions.Add("exact_room_title_verified");return room;}
        }
        throw new InvalidOperationException("exact_test_room_did_not_open");
    }
    [STAThread] public static int Main(string[] args){
        Console.InputEncoding=new UTF8Encoding(false,true);Console.OutputEncoding=new UTF8Encoding(false);SetProcessDPIAware();
        try{
            Require(args.Length==2,"mode_room_required");AllowRoom(args[1]);string mode=args[0];
            var allowed=new[]{"--inspect-pending-picker","--image-baseline","--media-draft-session","--media-send-session","--inspect-file-picker","--inspect-media","--open","--close","--draft-session","--send-session","--read-last-message","--message-baseline"};
            Require(allowed.Contains(mode),"unsupported_mode");
            using(var mutex=new Mutex(false,"Local\\NoteLineWindowPrep")){
                bool locked=false;
                try{
                    try{locked=mutex.WaitOne(0);}catch(AbandonedMutexException){locked=true;}
                    Require(locked,"another_navigation_running");Session();
                    if(mode=="--inspect-message-menu"){Console.WriteLine(new JavaScriptSerializer().Serialize(InspectMessageMenu()));return 0;}
                    if(mode=="--inspect-pending-picker"){Console.WriteLine(new JavaScriptSerializer().Serialize(InspectPendingPicker()));return 0;}
                    if(mode=="--image-baseline"){Console.WriteLine(new JavaScriptSerializer().Serialize(ImageBaseline()));return 0;}
                    if(mode=="--media-draft-session"||mode=="--media-send-session")return MediaSession(mode=="--media-send-session");
                    if(mode=="--inspect-file-picker"){Console.WriteLine(new JavaScriptSerializer().Serialize(InspectFilePicker()));return 0;}
                    if(mode=="--inspect-media"){Console.WriteLine(new JavaScriptSerializer().Serialize(InspectMedia()));return 0;}
                    if(mode=="--read-last-message"){Console.WriteLine(new JavaScriptSerializer().Serialize(ReadMessageOnce()));return 0;}
                    if(mode=="--message-baseline"){Console.WriteLine(new JavaScriptSerializer().Serialize(ReadMessageOnce(true)));return 0;}
                    if(mode=="--draft-session"||mode=="--send-session")return DraftSession(mode=="--send-session");
                    if(mode=="--close"){CloseEmptyRoom();Console.WriteLine(new JavaScriptSerializer().Serialize(new{status="closed",room=Room,send_invocations=0}));return 0;}
                    if(mode=="--production-draft-session"||mode=="--production-send-session")return DraftSession(mode=="--production-send-session");
                    if(mode=="--inspect"){
                        Console.WriteLine(new JavaScriptSerializer().Serialize(new {status="read_only",main=InspectWindow(Find("LINE"),"LINE"),room=InspectWindow(Find(Room),Room)}));return 0;
                    }
                    if(mode=="--close-empty-test-room"||mode=="--minimize-empty-test-room"){
                        var h=Find(Room);Require(h!=IntPtr.Zero,"test_room_not_open");Restore(h,Room);EmptyDraft(h);Guard(h,Room,true);
                        if(mode=="--close-empty-test-room")Require(PostMessage(h,0x0010,IntPtr.Zero,IntPtr.Zero),"close_request_failed");
                        else ShowWindow(h,6);
                        Thread.Sleep(300);Console.WriteLine(new JavaScriptSerializer().Serialize(new {status="test_window_state_changed",mode=mode,message_typed=false,send_invocations=0}));return 0;
                    }
                    stage="open";var hwnd=Open();Guard(hwnd,Room,true);
                    Console.WriteLine(new JavaScriptSerializer().Serialize(new {status=Production?"production_room_ready":"test_room_ready",room=Room,window_id=hwnd.ToInt64(),foreground=true,actions=actions,message_typed=false,send_invocations=0,observed_at=DateTimeOffset.UtcNow.ToString("o")}));return 0;
                }finally{if(locked)mutex.ReleaseMutex();}
            }
        }catch(Exception e){Console.WriteLine(new JavaScriptSerializer().Serialize(new {status="stopped",stage=stage,reason=e.GetType().Name,message=e.Message,actions=actions,message_typed=false,send_invocations=0}));return 2;}
    }
}
