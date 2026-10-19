// Image posting implementation: inspection entry point never enters or sends data.
using System;
using System.Linq;
using System.Windows.Automation;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Collections.Generic;
using System.Threading;

public partial class LineWindowPrep {
    static void FocusVerifiedCaption(IntPtr h,string title){
        Guard(h,title,false);Require(IsWindowEnabled(h)&&IsWindowVisible(h),"window_not_focusable");
        RECT r;Require(GetWindowRect(h,out r),"window_bounds_missing");
        var point=new POINT((r.left+r.right)/2,r.top+16);
        Require(GetAncestor(WindowFromPoint(point),2)==h,"caption_obscured");
        IntPtr hit;Require(EditMessage(h,0x0084,IntPtr.Zero,new IntPtr((point.y<<16)|(point.x&65535)),2,2000,out hit)!=IntPtr.Zero&&hit.ToInt32()==2,"caption_not_verified");
        Require(SetCursorPos(point.x,point.y),"cursor_move_failed");Guard(h,title,false);
        Require(GetAncestor(WindowFromPoint(point),2)==h,"caption_changed");
        var clicks=new[]{new INPUT{type=0,value=new INPUTUNION{mouse=new MOUSEINPUT{flags=2}}},new INPUT{type=0,value=new INPUTUNION{mouse=new MOUSEINPUT{flags=4}}}};
        Require(SendInput(2,clicks,System.Runtime.InteropServices.Marshal.SizeOf(typeof(INPUT)))==2,"caption_focus_unknown");
        Thread.Sleep(150);
    }
    static AutomationElement AttachControl(IntPtr h){
        Guard(h,Room,true);EmptyDraft(h);
        var input=DraftEdit(h).Current.BoundingRectangle;
        string path=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"line-attach-icon.png");
        using(var hash=System.Security.Cryptography.SHA256.Create())Require(BitConverter.ToString(hash.ComputeHash(File.ReadAllBytes(path))).Replace("-","")=="EC11905144B7D45649AF53440F24C83B6DBE01C195FADABCFE096CB0F8B2065C","attach_reference_changed");
        var found=new List<AutomationElement>();
        using(var reference=new Bitmap(path)){
            foreach(var e in Elements(Root(h,Room),ControlType.Group)){
                var b=e.Current.BoundingRectangle;
                if(e.Current.ClassName!="LcButton"||b.Top<input.Bottom||b.Width!=reference.Width||b.Height!=reference.Height||e.Current.IsOffscreen)continue;
                using(var current=new Bitmap(reference.Width,reference.Height)){
                    using(var g=Graphics.FromImage(current))g.CopyFromScreen((int)b.Left,(int)b.Top,0,0,current.Size);
                    bool same=true;for(int y=0;y<current.Height&&same;y++)for(int x=0;x<current.Width;x++)if(!SamePixel(reference.GetPixel(x,y),current.GetPixel(x,y))){same=false;break;}
                    if(same)found.Add(e);
                }
            }
        }
        Require(found.Count==1,"attach_icon_not_unique");return found[0];
    }
    [System.Runtime.InteropServices.DllImport("user32.dll")] static extern IntPtr GetWindow(IntPtr h,int command);
    [System.Runtime.InteropServices.DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr h,EnumProc callback,IntPtr a);
    [System.Runtime.InteropServices.DllImport("user32.dll")] static extern int GetDlgCtrlID(IntPtr h);
    [System.Runtime.InteropServices.DllImport("user32.dll",CharSet=System.Runtime.InteropServices.CharSet.Unicode,EntryPoint="SendMessageTimeoutW")] static extern IntPtr EditMessage(IntPtr h,uint message,IntPtr w,IntPtr l,uint flags,uint timeout,out IntPtr result);
    // GetWindowText/SetWindowText can expose the cached caption in another process.
    // Ask the edit control for its actual buffer, with a bounded cross-process call.
    static string PickerValue(IntPtr dialog){
        var edit=PickerControl(dialog,1148,"Edit");var buffer=System.Runtime.InteropServices.Marshal.AllocHGlobal(8192);
        try{IntPtr result;Require(EditMessage(edit,13,new IntPtr(4096),buffer,2,2000,out result)!=IntPtr.Zero,"filename_read_timeout");return System.Runtime.InteropServices.Marshal.PtrToStringUni(buffer);}
        finally{System.Runtime.InteropServices.Marshal.FreeHGlobal(buffer);}
    }
    static void SetPickerValue(IntPtr dialog,string value){
        var edit=PickerControl(dialog,1148,"Edit");var buffer=System.Runtime.InteropServices.Marshal.StringToHGlobalUni(value);
        try{IntPtr result;Require(EditMessage(edit,12,IntPtr.Zero,buffer,2,2000,out result)!=IntPtr.Zero&&result!=IntPtr.Zero,"filename_set_failed");}
        finally{System.Runtime.InteropServices.Marshal.FreeHGlobal(buffer);}
        Require(PickerValue(dialog)==value,"filename_mismatch");
    }
    static IntPtr Picker(IntPtr h){
        var dialogs=new List<IntPtr>();uint pid;GetWindowThreadProcessId(h,out pid);
        EnumWindows((w,a)=>{uint p;GetWindowThreadProcessId(w,out p);if(p==pid&&IsWindowVisible(w)&&WindowClass(w)=="#32770"&&GetWindow(w,4)==h)dialogs.Add(w);return true;},IntPtr.Zero);
        Require(dialogs.Count<=1,"file_dialog_not_unique");return dialogs.Count==1?dialogs[0]:IntPtr.Zero;
    }
    static IntPtr PickerControl(IntPtr dialog,int id,string cls){
        var matches=new List<IntPtr>();EnumChildWindows(dialog,(w,a)=>{if(GetDlgCtrlID(w)==id&&WindowClass(w)==cls)matches.Add(w);return true;},IntPtr.Zero);
        Require(matches.Count==1,"file_control_not_unique");Require(IsWindowVisible(matches[0])&&IsWindowEnabled(matches[0]),"file_control_unavailable");return matches[0];
    }
    static IntPtr OpenPicker(IntPtr h){
        EmptyDraft(h);Require(Picker(h)==IntPtr.Zero,"existing_file_dialog_preserved");
        var button=AttachControl(h);var b=button.Current.BoundingRectangle;
        var point=new POINT((int)(b.Left+b.Width/2),(int)(b.Top+b.Height/2));
        Require(GetAncestor(WindowFromPoint(point),2)==h,"attach_obscured");
        Require(SetCursorPos(point.x,point.y),"cursor_move_failed");Guard(h,Room,true);
        var clicks=new[]{new INPUT{type=0,value=new INPUTUNION{mouse=new MOUSEINPUT{flags=2}}},new INPUT{type=0,value=new INPUTUNION{mouse=new MOUSEINPUT{flags=4}}}};
        Require(SendInput(2,clicks,System.Runtime.InteropServices.Marshal.SizeOf(typeof(INPUT)))==2,"attach_click_unknown");
        IntPtr dialog=IntPtr.Zero;
        for(int n=0;n<50;n++){Thread.Sleep(100);dialog=Picker(h);if(dialog!=IntPtr.Zero)break;}
        Require(dialog!=IntPtr.Zero&&Title(dialog)=="開く"&&!IsWindowEnabled(h),"file_dialog_missing");return dialog;
    }
    static void ClosePicker(IntPtr h,IntPtr dialog,string expected){
        Require(Picker(h)==dialog&&Title(dialog)=="開く","file_dialog_changed");
        Require(PickerValue(dialog)==expected,"changed_filename_preserved");
        Require(PostMessage(dialog,16,IntPtr.Zero,IntPtr.Zero),"cancel_file_dialog_failed");
        for(int n=0;n<30&&Picker(h)!=IntPtr.Zero;n++)Thread.Sleep(100);
        Restore(h,Room);EmptyDraft(h);
    }
    static object InspectFilePicker(){
        var h=Find(Room);Require(h!=IntPtr.Zero,"room_not_open");Restore(h,Room);var dialog=OpenPicker(h);
        try{
            var input=PickerControl(dialog,1148,"Edit");var open=PickerControl(dialog,1,"Button");var cancel=PickerControl(dialog,2,"Button");
            return new{status="file_picker_inspected",room=Room,filename_empty=PickerValue(dialog)=="",open_label=Title(open),cancel_label=Title(cancel),send_invocations=0};
        }finally{ClosePicker(h,dialog,"");}
    }
    static object InspectPendingPicker(){
        var h=Find(Room);Require(h!=IntPtr.Zero,"room_not_open");Guard(h,Room,false);
        var dialog=Picker(h);Require(dialog!=IntPtr.Zero,"owned_picker_missing");
        SetForegroundWindow(dialog);Thread.Sleep(200);
        Require(GetForegroundWindow()==dialog,"picker_not_foreground");
        RECT rect;Require(GetWindowRect(dialog,out rect),"picker_bounds_missing");string png;
        using(var bitmap=new Bitmap(rect.right-rect.left,rect.bottom-rect.top)){
            using(var graphics=Graphics.FromImage(bitmap))graphics.CopyFromScreen(rect.left,rect.top,0,0,bitmap.Size);
            using(var stream=new MemoryStream()){bitmap.Save(stream,ImageFormat.Png);png=Convert.ToBase64String(stream.ToArray());}
        }
        return new{status="pending_picker",room=Room,filename=PickerValue(dialog),open_label=Title(PickerControl(dialog,1,"Button")),capture_png=png,send_invocations=0};
    }
    class MediaRequest{public string action{get;set;}public string path{get;set;}public string sha256{get;set;}}
    static string MediaHash(string path){using(var hash=System.Security.Cryptography.SHA256.Create())return BitConverter.ToString(hash.ComputeHash(File.ReadAllBytes(path))).Replace("-","").ToLowerInvariant();}
    static int MediaSession(bool allow){
        var json=new System.Web.Script.Serialization.JavaScriptSerializer();IntPtr h=IntPtr.Zero,dialog=IntPtr.Zero;
        string bound=null,path=null,hash=null;bool prepared=false,invoked=false;var age=new System.Diagnostics.Stopwatch();
        Console.WriteLine(json.Serialize(new{status="session_ready",room=Room,send_enabled=allow}));Console.Out.Flush();
        string line;
        while((line=Console.ReadLine())!=null){
            try{
                Require(line.Length<10000,"request_too_large");var r=json.Deserialize<MediaRequest>(line);
                if(r.action=="preflight"){
                    Require(bound==null,"already_bound");Require(System.IO.Path.IsPathRooted(r.path)&&r.path.EndsWith(".png",StringComparison.OrdinalIgnoreCase),"image_path_invalid");
                    Require(new FileInfo(r.path).Length<=20*1024*1024,"image_too_large");Require(MediaHash(r.path)==r.sha256,"image_changed");
                    h=Find(Room);Require(h!=IntPtr.Zero,"room_not_open");Guard(h,Room,true);EmptyDraft(h);AttachControl(h);
                    path=r.path;hash=r.sha256;bound=Instance(h);age.Start();
                }else{
                    Require(bound!=null&&Instance(h)==bound,"window_instance_changed");Session();
                    if(r.action=="prepare"){
                        Require(!prepared&&!invoked&&age.Elapsed.TotalSeconds<30,"prepare_expired");
                        dialog=OpenPicker(h);Require(PickerValue(dialog)=="","existing_filename_preserved");
                        prepared=true;SetPickerValue(dialog,path);
                    }else if(r.action=="verify"||r.action=="invoke"){
                        Require(prepared&&!invoked&&age.Elapsed.TotalSeconds<30,"prepared_image_expired");
                        Require(Picker(h)==dialog&&Title(dialog)=="開く"&&GetForegroundWindow()==dialog&&!IsWindowEnabled(h),"file_dialog_changed");
                        Require(PickerValue(dialog)==path&&MediaHash(path)==hash,"prepared_image_changed");
                        var open=PickerControl(dialog,1,"Button");Require(Title(open)=="開く(&O)"&&IsWindowEnabled(open),"open_button_changed");
                        if(r.action=="invoke"){
                            Require(allow,"send_disabled");Require(LineDeliveryPolicy.InWindow(DateTimeOffset.UtcNow),"outside_delivery_window");
                            invoked=true;Require(PostMessage(open,0x00F5,IntPtr.Zero,IntPtr.Zero),"image_send_outcome_unknown");Thread.Sleep(300);
                        }
                    }else if(r.action=="clear"){
                        Require(prepared&&!invoked,"clear_not_allowed");ClosePicker(h,dialog,path);prepared=false;dialog=IntPtr.Zero;
                    }else throw new InvalidOperationException("unsupported_action");
                }
                Console.WriteLine(json.Serialize(new{status="ok",action=r.action,room=Room,window_instance=bound,prepared=prepared,send_invocations=invoked?1:0}));
            }catch(Exception error){Console.WriteLine(json.Serialize(new{status="stopped",reason=error is InvalidOperationException?error.Message:error.GetType().Name,send_invocations=invoked?1:0}));Console.Out.Flush();return 2;}
            Console.Out.Flush();
        }
        return 0;
    }
    static object ImageBaseline(){
        var h=Find(Room);Require(h!=IntPtr.Zero,"room_not_open");Restore(h,Room);EmptyDraft(h);
        var row=LatestMessage(h);var b=row.Current.BoundingRectangle;string png;
        using(var bitmap=new Bitmap((int)b.Width,(int)b.Height)){
            using(var g=Graphics.FromImage(bitmap))g.CopyFromScreen((int)b.Left,(int)b.Top,0,0,bitmap.Size);
            using(var output=new MemoryStream()){bitmap.Save(output,ImageFormat.Png);png=Convert.ToBase64String(output.ToArray());}
        }
        return new{status="image_baseline",room=Room,window_instance=Instance(h),last_message_id=RowId(row),visible_message_ids=VisibleRowIds(h),row_png=png,row_bounds=new[]{b.Left,b.Top,b.Width,b.Height},send_invocations=0};
    }
    static object InspectMedia(){
        var h=Find(Room);Require(h!=IntPtr.Zero,"room_not_open");Restore(h,Room);EmptyDraft(h);
        var root=Root(h,Room);
        var buttons=Elements(root,ControlType.Button).Select(e=>new {name=e.Current.Name,
            automation_id=e.Current.AutomationId,cls=e.Current.ClassName,
            bounds=e.Current.BoundingRectangle.ToString(),enabled=e.Current.IsEnabled,
            patterns=e.GetSupportedPatterns().Select(p=>p.ProgrammaticName).ToArray()}).ToArray();
        var input=DraftEdit(h).Current.BoundingRectangle;
        var lower=root.FindAll(TreeScope.Descendants,Condition.TrueCondition).Cast<AutomationElement>()
            .Where(e=>e.Current.BoundingRectangle.Top>=input.Top-50&&e.Current.ControlType!=ControlType.ListItem)
            .Select(e=>new{type=e.Current.ControlType.ProgrammaticName,name=e.Current.Name,cls=e.Current.ClassName,
                automation_id=e.Current.AutomationId,bounds=e.Current.BoundingRectangle.ToString(),
                patterns=e.GetSupportedPatterns().Select(p=>p.ProgrammaticName).ToArray()}).ToArray();
        string png=null,captureError=null;
        try{
            RECT rect;Require(GetWindowRect(h,out rect),"window_bounds_missing");
            using(var bitmap=new Bitmap(rect.right-rect.left,rect.bottom-rect.top)){
                using(var graphics=Graphics.FromImage(bitmap))graphics.CopyFromScreen(rect.left,rect.top,0,0,bitmap.Size);
                using(var stream=new MemoryStream()){bitmap.Save(stream,ImageFormat.Png);png=Convert.ToBase64String(stream.ToArray());}
            }
        }catch(Exception error){captureError=error.GetType().Name;}
        return new{status="media_controls",room=Room,window_instance=Instance(h),buttons=buttons,lower_controls=lower,capture_png=png,capture_error=captureError,send_invocations=0};
    }
}
