using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Automation;

public partial class LineWindowPrep {
    static readonly string Folder=Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,".."));
    static bool pcHistoryDownloadAllowed;
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h,StringBuilder text,int count);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern IntPtr SendMessage(IntPtr h,int message,IntPtr w,string value);
    [DllImport("user32.dll",CharSet=CharSet.Unicode,EntryPoint="SendMessageW")] static extern IntPtr ReadControl(IntPtr h,int message,IntPtr w,StringBuilder value);
    [DllImport("user32.dll")] static extern IntPtr GetWindow(IntPtr h,uint command);
    [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr h,EnumProc fn,IntPtr arg);
    [DllImport("user32.dll")] static extern int GetDlgCtrlID(IntPtr h);
    [DllImport("user32.dll")] static extern bool IsWindowEnabled(IntPtr h);
    static string Class(IntPtr h){var s=new StringBuilder(256);GetClassName(h,s,256);return s.ToString();}
    static uint PID(IntPtr h){uint p;GetWindowThreadProcessId(h,out p);return p;}
    static string ControlText(IntPtr h){var text=new StringBuilder(2048);ReadControl(h,13,new IntPtr(text.Capacity),text);return text.ToString();}
    static List<IntPtr> Popups(IntPtr room){
        var all=new List<IntPtr>();uint pid=PID(room);
        EnumWindows((h,a)=>{if(IsWindowVisible(h)&&PID(h)==pid&&Class(h)=="Qt663QWindowPopupSaveBits")all.Add(h);return true;},IntPtr.Zero);
        return all;
    }
    static void Click(IntPtr context,string title,int x,int y,bool popup){
        Guard(context,title,!popup);
        var hit=WindowFromPoint(new POINT(x,y));
        Require(PID(hit)==PID(context),"target_obscured");
        if(popup)Require(Popups(context).Contains(GetAncestor(hit,2)),"menu_context_changed");
        else Require(GetAncestor(hit,2)==context,"target_context_changed");
        Require(SetCursorPos(x,y),"pointer_failed");
        var input=new[]{new INPUT{type=0,value=new INPUTUNION{mouse=new MOUSEINPUT{flags=2}}},new INPUT{type=0,value=new INPUTUNION{mouse=new MOUSEINPUT{flags=4}}}};
        Require(SendInput(2,input,Marshal.SizeOf(typeof(INPUT)))==2,"click_incomplete");
    }
    static IntPtr Menu(IntPtr room){
        Guard(room,Room,true);RequireDraftIsEmpty(room);Require(Popups(room).Count==0,"existing_menu_preserved");
        var root=Root(room,Room);var bounds=root.Current.BoundingRectangle;
        // Observed Qt header control, not the message composer or title-bar controls.
        var buttons=Elements(root,ControlType.Group).Where(e=>{
            var b=e.Current.BoundingRectangle;return e.Current.ClassName=="LcButton"&&b.Top>bounds.Top+40&&b.Top<bounds.Top+160&&b.Right>bounds.Right-65&&b.Width<b.Height*0.8&&b.Height>=20&&b.Height<50;
        }).ToList();
        Require(buttons.Count==1,"header_menu_not_unique");var r=buttons[0].Current.BoundingRectangle;
        Click(room,Room,(int)(r.Left+r.Width/2),(int)(r.Top+r.Height/2),false);
        for(int i=0;i<20;i++){Thread.Sleep(100);Session();var menus=Popups(room);if(menus.Count==1)return menus[0];Require(menus.Count<2,"multiple_menus");}
        throw new InvalidOperationException("menu_missing");
    }
    static Bitmap Capture(IntPtr hwnd){
        RECT r;Require(GetWindowRect(hwnd,out r),"bounds_missing");
        Require(r.right-r.left>10&&r.bottom-r.top>10&&r.right-r.left<2000&&r.bottom-r.top<2000,"bounds_invalid");
        var bitmap=new Bitmap(r.right-r.left,r.bottom-r.top,PixelFormat.Format24bppRgb);
        using(var g=Graphics.FromImage(bitmap))g.CopyFromScreen(r.left,r.top,0,0,bitmap.Size);
        return bitmap;
    }
    static void AllowedRoom(string name){
        var config=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(Folder,"collector-config.json"),Encoding.UTF8));
        var rooms=(System.Collections.IEnumerable)config["rooms"];
        var matches=rooms.Cast<Dictionary<string,object>>().Where(r=>(string)r["name"]==name).ToList();
        Require(matches.Count==1,"room_not_registered");
        pcHistoryDownloadAllowed=!matches[0].ContainsKey("pc_history_download")||(bool)matches[0]["pc_history_download"];
        Require(!String.IsNullOrWhiteSpace(name)&&name.Length<200&&!name.Any(Char.IsControl),"room_invalid");Room=name;
    }
    static bool LabelMatches(Bitmap image,RECT popup,System.Windows.Rect text,Bitmap label){
        if((int)text.Width!=label.Width||(int)text.Height!=label.Height)return false;
        int x=(int)text.Left-popup.left,y=(int)text.Top-popup.top;
        if(x<0||y<0||x+label.Width>image.Width||y+label.Height>image.Height)return false;
        // UIA's fractional bounds can round by 1-2 pixels at 150% DPI.
        for(int dy=-3;dy<=3;dy++)for(int dx=-3;dx<=3;dx++){
            if(x+dx<0||y+dy<0||x+dx+label.Width>image.Width||y+dy+label.Height>image.Height)continue;
            int differences=0,ink=0;
            for(int j=0;j<label.Height;j++)for(int i=0;i<label.Width;i++){
                var a=label.GetPixel(i,j);var b=image.GetPixel(x+i+dx,y+j+dy);
                bool aa=a.R+a.G+a.B<420,bb=b.R+b.G+b.B<420;
                if(aa)ink++;if(aa!=bb)differences++;
            }
            if(ink>150&&differences<=8)return true;
        }
        return false;
    }
    static void ChooseSave(IntPtr room,IntPtr popup){
        RECT bounds;Require(GetWindowRect(popup,out bounds),"menu_bounds_missing");
        var labels=Elements(AutomationElement.FromHandle(popup),ControlType.Group).Where(e=>e.Current.ClassName=="LcText").ToList();
        using(var template=new Bitmap(Path.Combine(Folder,"native/save-label-150.png")))
        using(var bitmap=Capture(popup)){
            var matches=labels.Where(e=>LabelMatches(bitmap,bounds,e.Current.BoundingRectangle,template)).ToList();
            Require(matches.Count==1,"save_label_not_unique_or_display_scale_changed");
            var r=matches[0].Current.BoundingRectangle;
            using(var confirm=Capture(popup))Require(LabelMatches(confirm,bounds,r,template),"save_label_changed");
            Click(room,Room,(int)(r.Left+r.Width/2),(int)(r.Top+r.Height/2),true);
        }
    }
    static IntPtr SaveDialog(IntPtr room){
        Guard(room,Room,false);
        var matches=new List<IntPtr>();
        EnumWindows((h,a)=>{
            if(IsWindowVisible(h)&&Class(h)=="#32770"&&Title(h)=="名前を付けて保存"&&PID(h)==PID(room)){
                IntPtr owner=GetWindow(h,4);for(int i=0;i<5&&owner!=IntPtr.Zero;i++){
                    if(owner==room){matches.Add(h);break;}owner=GetWindow(owner,4);
                }
            }return true;
        },IntPtr.Zero);
        Require(matches.Count<=1,"multiple_save_dialogs");return matches.Count==1?matches[0]:IntPtr.Zero;
    }
    static IntPtr DialogControl(IntPtr dialog,int id,string cls){
        var found=new List<IntPtr>();EnumChildWindows(dialog,(h,a)=>{if(GetDlgCtrlID(h)==id&&Class(h)==cls)found.Add(h);return true;},IntPtr.Zero);
        Require(found.Count==1,"save_control_not_unique");return found[0];
    }
    [DllImport("user32.dll")] static extern bool PrintWindow(IntPtr hwnd,IntPtr dc,uint flags);
    [DllImport("user32.dll",EntryPoint="GetWindowLongW")] static extern int WindowStyle(IntPtr hwnd,int index);
    static Bitmap CaptureDialog(IntPtr hwnd){
        RECT r;Require(GetWindowRect(hwnd,out r),"error_bounds_missing");
        Require(r.right-r.left>10&&r.bottom-r.top>10&&r.right-r.left<1000&&r.bottom-r.top<1000,"error_bounds_invalid");
        var bitmap=new Bitmap(r.right-r.left,r.bottom-r.top,PixelFormat.Format24bppRgb);
        using(var g=Graphics.FromImage(bitmap)){
            g.Clear(Color.Magenta);var dc=g.GetHdc();
            try{PrintWindow(hwnd,dc,2);}finally{g.ReleaseHdc(dc);}
        }
        return bitmap;
    }
    static bool DownloadFailureMatches(Bitmap bitmap){
        var reference=Path.Combine(Folder,"native/download-failed-150.png");
        using(var hash=System.Security.Cryptography.SHA256.Create())
            Require(BitConverter.ToString(hash.ComputeHash(File.ReadAllBytes(reference))).Replace("-","")=="59B18E1A4C2FEAF11C7970EADF1B3F05D9EC64433FED30B32E9C561BFDC53BF9","download_error_reference_changed");
        using(var template=new Bitmap(reference)){
            if(bitmap.Size!=template.Size)return false;
            int differences=0,ink=0;
            for(int y=0;y<bitmap.Height;y++)for(int x=0;x<bitmap.Width;x++){
                var a=template.GetPixel(x,y);var b=bitmap.GetPixel(x,y);
                bool dark=a.R+a.G+a.B<420;if(dark)ink++;
                if(dark!=(b.R+b.G+b.B<420))differences++;
            }
            return ink>150&&differences<=12;
        }
    }
    static bool DismissDownloadFailure(IntPtr room){
        var dialogs=new List<IntPtr>();EnumWindows((h,a)=>{
            if(IsWindowVisible(h)&&PID(h)==PID(room)&&GetWindow(h,4)==room&&Class(h)=="Qt663QWindowToolSaveBits"&&Title(h)=="LINE")dialogs.Add(h);
            return true;
        },IntPtr.Zero);
        if(dialogs.Count!=1)return false;
        var dialog=dialogs[0];
        bool verified;
        using(var bitmap=Capture(dialog))verified=DownloadFailureMatches(bitmap);
        // Qt's owned dialog can be blank in a desktop copy. Ask that exact
        // window to render, retaining the same full-dialog identity check.
        if(!verified)using(var bitmap=CaptureDialog(dialog))verified=DownloadFailureMatches(bitmap);
        if(!verified)return false;
        Guard(room,Room,false);
        Require(GetWindow(dialog,4)==room&&PID(dialog)==PID(room)&&IsWindowVisible(dialog),"error_owner_changed");
        bool wasTopmost=(WindowStyle(dialog,-20)&8)!=0;
        try{
        // Only the verified informational dialog is raised temporarily. Always
        // restore its previous topmost state, including on refusal or failure.
        Require(SetWindowPos(dialog,new IntPtr(-1),0,0,0,0,0x0043),"error_focus_failed");
        SetForegroundWindow(dialog);Thread.Sleep(150);
        using(var confirm=CaptureDialog(dialog))Require(DownloadFailureMatches(confirm),"error_dialog_changed");
        Guard(room,Room,false);RECT r;Require(GetWindowRect(dialog,out r),"error_bounds_missing");
        // This exact informational dialog contains only the acknowledged OK.
        int px=r.left+210,py=r.top+128;
        Require(GetAncestor(WindowFromPoint(new POINT(px,py)),2)==dialog,"error_obscured");
        Require(GetWindow(dialog,4)==room&&PID(dialog)==PID(room),"error_owner_changed");
        Require(SetCursorPos(px,py),"pointer_failed");
        var input=new[]{new INPUT{type=0,value=new INPUTUNION{mouse=new MOUSEINPUT{flags=2}}},new INPUT{type=0,value=new INPUTUNION{mouse=new MOUSEINPUT{flags=4}}}};
        Require(SendInput(2,input,Marshal.SizeOf(typeof(INPUT)))==2,"error_ack_incomplete");
        for(int i=0;i<10;i++){Thread.Sleep(100);if(!IsWindowVisible(dialog))return true;}
        throw new InvalidOperationException("error_ack_not_confirmed");
        }finally{
            if(!wasTopmost&&GetWindow(dialog,4)==room&&PID(dialog)==PID(room))
                SetWindowPos(dialog,new IntPtr(-2),0,0,0,0,0x0013);
        }
    }
    static string Export(IntPtr room,string target){
        target=Path.GetFullPath(target);
        var exports=Path.GetFullPath(Path.Combine(Folder,"state/exports"));
        Guid id;
        Require(Path.GetDirectoryName(target)==exports&&Path.GetExtension(target)==".txt"&&Guid.TryParseExact(Path.GetFileNameWithoutExtension(target),"N",out id),"export_path_invalid");
        Require(Directory.Exists(exports)&&!File.Exists(target),"export_destination_not_ready");
        Require(SaveDialog(room)==IntPtr.Zero,"existing_save_dialog_preserved");
        stage="menu";var popup=Menu(room);stage="save_label";
        try{ChooseSave(room,popup);}catch{if(IsWindowVisible(popup)&&PID(popup)==PID(room))PostMessage(popup,16,IntPtr.Zero,IntPtr.Zero);throw;}
        stage="save_dialog";IntPtr dh=IntPtr.Zero;
        for(int i=0;i<75;i++){Thread.Sleep(200);dh=SaveDialog(room);if(dh!=IntPtr.Zero)break;if(DismissDownloadFailure(room))throw new InvalidOperationException("line_download_failed");}
        Require(dh!=IntPtr.Zero,"save_dialog_missing_or_download_failed");
        var eh=DialogControl(dh,1001,"Edit");var bh=DialogControl(dh,1,"Button");
        Require(ControlText(eh)=="[LINE]"+Room+".txt","export_room_filename_mismatch");
        Require(ControlText(bh)=="保存(&S)"||ControlText(bh)=="保存(S)","save_button_label_mismatch");
        Require(PID(dh)==PID(room)&&PID(eh)==PID(room)&&PID(bh)==PID(room),"save_process_mismatch");
        Require(GetAncestor(eh,2)==dh&&GetAncestor(bh,2)==dh,"save_control_parent_mismatch");
        Guard(room,Room,false);Require(GetForegroundWindow()==dh,"save_dialog_not_foreground");
        // Native common-dialog controls do not provide UIA Value/Invoke patterns.
        SendMessage(eh,12,IntPtr.Zero,target);Require(ControlText(eh)==target,"export_path_not_set");
        Guard(room,Room,false);Require(GetForegroundWindow()==dh&&!File.Exists(target),"save_context_changed");
        SendMessage(bh,245,IntPtr.Zero,null);
        stage="verify_export";long prior=-1;int stable=0;
        for(int i=0;i<50;i++){
            Thread.Sleep(200);Guard(room,Room,false);
            if(File.Exists(target)){
                long size=new FileInfo(target).Length;
                stable=size>0&&size==prior?stable+1:0;prior=size;
                if(stable>=3&&SaveDialog(room)==IntPtr.Zero){RequireDraftIsEmpty(room);return target;}
            }
        }
        throw new InvalidOperationException("export_not_confirmed");
    }
    static string CloseRoom(){
        Require(Room!="LINE","main_window_close_refused");
        var room=Find(Room);
        if(room==IntPtr.Zero||!IsWindowVisible(room))return "already_closed";
        Guard(room,Room,false);
        Require(IsWindowEnabled(room)&&SaveDialog(room)==IntPtr.Zero,"room_dialog_preserved");
        Require(Popups(room).Count==0,"line_menu_preserved");
        if(IsIconic(room))Restore(room,Room);
        RequireDraftIsEmpty(room);
        Guard(room,Room,false);
        // WM_CLOSE closes only this exact separate chat window; never the
        // LINE process and never a menu's leave/delete action.
        Require(PostMessage(room,16,IntPtr.Zero,IntPtr.Zero),"room_close_failed");
        for(int i=0;i<20;i++){
            Thread.Sleep(100);
            if(!Identity(room,Room)||!IsWindowVisible(room))return "closed";
        }
        throw new InvalidOperationException("room_close_not_confirmed");
    }
    static void ReportCleanup(){
        try{Console.Error.WriteLine(new JavaScriptSerializer().Serialize(new{kind="window_cleanup",status=CloseRoom()}));}
        catch(Exception e){Console.Error.WriteLine(new JavaScriptSerializer().Serialize(new{kind="window_cleanup",status="preserved",reason=e is InvalidOperationException?e.Message:e.GetType().Name}));}
    }
    [STAThread] public static int Main(string[] args){
        Console.OutputEncoding=new UTF8Encoding(false);SetProcessDPIAware();
        try{
            Require(args.Length>=2,"mode_room_required");Require(new[]{"--open","--menu","--export","--inspect-error","--close","--inspect-history","--inspect-copy-menu","--read-screen"}.Contains(args[0]),"unsupported_mode");Require(args[0]=="--read-screen"?(args.Length>=2&&args.Length<=4):args.Length==(args[0]=="--export"?3:2),"arguments_invalid");AllowedRoom(args[1]);
            if(args[0]=="--export")Require(pcHistoryDownloadAllowed,"pc_history_download_disabled");
            using(var mutex=new Mutex(false,"Local\\NoteLineWindowPrep")){
                bool locked=false;try{
                    try{locked=mutex.WaitOne(0);}catch(AbandonedMutexException){locked=true;}
                    Require(locked,"line_busy");Session();
                    if(args[0]=="--close"){
                        Console.WriteLine(new JavaScriptSerializer().Serialize(new{status=CloseRoom(),room=Room,send_invocations=0}));return 0;
                    }
                    if(args[0]=="--inspect-error"){
                        var found=Find(Room);Require(found!=IntPtr.Zero,"room_missing");
                        var windows=new List<object>();EnumWindows((h,a)=>{
                            if(IsWindowVisible(h)&&PID(h)==PID(found)&&GetWindow(h,4)==found){
                                RECT rr;GetWindowRect(h,out rr);string pp;using(var b=Capture(h))using(var s=new MemoryStream()){b.Save(s,ImageFormat.Png);pp=Convert.ToBase64String(s.ToArray());}
                                string rendered=null;if(Class(h)=="Qt663QWindowToolSaveBits")using(var b=CaptureDialog(h))using(var stream=new MemoryStream()){b.Save(stream,ImageFormat.Png);rendered=Convert.ToBase64String(stream.ToArray());} windows.Add(new{hwnd=h.ToInt64(),cls=Class(h),title=Title(h),x=rr.left,y=rr.top,width=rr.right-rr.left,height=rr.bottom-rr.top,png=pp,rendered_png=rendered});
                            }return true;
                        },IntPtr.Zero);Console.WriteLine(new JavaScriptSerializer().Serialize(windows));return 0;
                    }
                    var existing=Find(Room);if(existing!=IntPtr.Zero&&DismissDownloadFailure(existing))throw new InvalidOperationException("line_download_failed");
                    var room=Open();
                    if(args[0]=="--read-screen"){
                        var serializer=new JavaScriptSerializer();serializer.MaxJsonLength=16*1024*1024;
                        Console.WriteLine(serializer.Serialize(ReadScreen(room,args.Length>=3?args[2]:"",args.Length==4?Int32.Parse(args[3]):0)));
                    }else if(args[0]=="--inspect-copy-menu"){
                        Console.WriteLine(new JavaScriptSerializer().Serialize(InspectCopyMenu(room)));
                    }else if(args[0]=="--inspect-history"){
                        var serializer=new JavaScriptSerializer();serializer.MaxJsonLength=16*1024*1024;
                        Console.WriteLine(serializer.Serialize(InspectHistory(room)));
                    }else if(args[0]=="--menu"){
                        stage="menu";var popup=Menu(room);stage="capture";string png;using(var bmp=Capture(popup))using(var stream=new MemoryStream()){bmp.Save(stream,ImageFormat.Png);png=Convert.ToBase64String(stream.ToArray());}
                        stage="menu_tree";
                        var root=AutomationElement.FromHandle(popup);
                        var items=root.FindAll(TreeScope.Descendants,Condition.TrueCondition).Cast<AutomationElement>().Select(e=>new {name=e.Current.Name,type=e.Current.ControlType.ProgrammaticName,cls=e.Current.ClassName,bounds=e.Current.BoundingRectangle.ToString()}).ToArray();
                        Console.WriteLine(new JavaScriptSerializer().Serialize(new{status="menu_captured",items=items,png=png,send_invocations=0}));
                        if(IsWindowVisible(popup)&&PID(popup)==PID(room))PostMessage(popup,16,IntPtr.Zero,IntPtr.Zero);
                    }else if(args[0]=="--export"){
                        var path=Export(room,args[2]);Console.WriteLine(new JavaScriptSerializer().Serialize(new{status="exported",room=Room,path=path,send_invocations=0}));
                    }else Console.WriteLine(new JavaScriptSerializer().Serialize(new{status="room_ready",room=Room,send_invocations=0}));
                    return 0;
                }finally{if(locked){try{if(args[0]=="--export"||args[0]=="--inspect-history"||args[0]=="--inspect-copy-menu"||args[0]=="--read-screen")ReportCleanup();}finally{mutex.ReleaseMutex();}}}
            }
        }catch(Exception e){Console.WriteLine(new JavaScriptSerializer().Serialize(new{status="stopped",room=Room,stage=stage,reason=e is InvalidOperationException?e.Message:e.GetType().Name,detail=e.Message,png=screenDiagnostic,send_invocations=0}));return 2;}
    }
}
