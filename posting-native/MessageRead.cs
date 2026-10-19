// Exact text inspection through LINE's own Copy menu. No send or message editing.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Automation;
using Forms=System.Windows.Forms;

public partial class LineWindowPrep {
    static bool lastReadOutgoing=false;
    [DllImport("user32.dll")] static extern uint GetClipboardSequenceNumber();
    [DllImport("user32.dll")] static extern IntPtr GetClipboardOwner();
    static bool SamePixel(Color a,Color b){return Math.Abs(a.R-b.R)<=8&&Math.Abs(a.G-b.G)<=8&&Math.Abs(a.B-b.B)<=8;}
    // Reference is the visually reviewed Japanese Copy label, not a menu ordinal.
    // Unique full-pixel match; a new DPI/theme/font fails closed rather than clicking.
    static POINT CopyLabelPoint(Bitmap bitmap){
        string reference=System.IO.Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"line-copy-label.png");
        using(var hash=System.Security.Cryptography.SHA256.Create())
            Require(BitConverter.ToString(hash.ComputeHash(System.IO.File.ReadAllBytes(reference))).Replace("-","")
                    =="37A88EF330991E6610D5FA1224D5B370D5C44BEC8889193D5E0FC6957D4402A1","copy_reference_changed");
        using(var label=new Bitmap(reference)){
            Require(label.Width==55&&label.Height==34,"copy_reference_invalid");
            var ink=new List<POINT>();for(int y=0;y<label.Height;y++)for(int x=0;x<label.Width;x++)
                if(label.GetPixel(x,y).R<150)ink.Add(new POINT(x,y));
            Require(ink.Count>20,"copy_reference_empty");var found=new List<POINT>();
            for(int y=0;y<=bitmap.Height-label.Height;y++)for(int x=0;x<=bitmap.Width-label.Width;x++){
                bool good=true;
                foreach(var p in ink){if(!SamePixel(label.GetPixel(p.x,p.y),bitmap.GetPixel(x+p.x,y+p.y))){good=false;break;}}
                if(!good)continue;
                for(int yy=0;yy<label.Height&&good;yy++)for(int xx=0;xx<label.Width;xx++)
                    if(!SamePixel(label.GetPixel(xx,yy),bitmap.GetPixel(x+xx,y+yy))){good=false;break;}
                if(good)found.Add(new POINT(x+label.Width/2,y+label.Height/2));
            }
            Require(found.Count==1,"copy_visual_label_not_unique_"+found.Count);return found[0];
        }
    }
    static string WindowClass(IntPtr h){var s=new System.Text.StringBuilder(256);GetClassName(h,s,256);return s.ToString();}
    static IntPtr CopyPopup(IntPtr h){
        uint pid;GetWindowThreadProcessId(h,out pid);var matches=new List<IntPtr>();
        EnumWindows((w,a)=>{uint owner;GetWindowThreadProcessId(w,out owner);
            if(owner==pid&&IsWindowVisible(w)&&WindowClass(w)=="Qt663QWindowPopupSaveBits")matches.Add(w);return true;},IntPtr.Zero);
        Require(matches.Count==1,"copy_menu_not_unique");return matches[0];
    }
    static string ReadLastMessage(IntPtr h){
        try{
            stage="read_menu_open";LastMessageMenu(h);var popup=CopyPopup(h);RECT r;Require(GetWindowRect(popup,out r),"menu_bounds_missing");
            Require(r.right-r.left>50&&r.right-r.left<800&&r.bottom-r.top>50&&r.bottom-r.top<1200,"menu_bounds_invalid");
            POINT point;
            stage="read_visual_copy_label";
            using(var bitmap=new Bitmap(r.right-r.left,r.bottom-r.top)){
                using(var g=Graphics.FromImage(bitmap))g.CopyFromScreen(r.left,r.top,0,0,bitmap.Size);
                var relative=CopyLabelPoint(bitmap);point=new POINT(r.left+relative.x,r.top+relative.y);
            }
            Require(CopyPopup(h)==popup&&GetAncestor(WindowFromPoint(point),2)==popup,"copy_menu_changed");
            // Preserve existing formats in memory only; never log clipboard contents.
            uint before=GetClipboardSequenceNumber(),copied=0;
            stage="read_clipboard_snapshot";var saved=new Forms.DataObject();var original=Forms.Clipboard.GetDataObject();
            if(original!=null)foreach(string format in original.GetFormats(false))saved.SetData(format,false,original.GetData(format,false));
            Require(GetClipboardSequenceNumber()==before,"clipboard_changed_before_copy");
            try{
                stage="read_copy_click";Require(SetCursorPos(point.x,point.y),"cursor_move_failed");
                Require(CopyPopup(h)==popup&&GetAncestor(WindowFromPoint(point),2)==popup,"copy_menu_changed");
                var clicks=new[]{new INPUT{type=0,value=new INPUTUNION{mouse=new MOUSEINPUT{flags=2}}},new INPUT{type=0,value=new INPUTUNION{mouse=new MOUSEINPUT{flags=4}}}};
                Require(SendInput(2,clicks,Marshal.SizeOf(typeof(INPUT)))==2,"copy_click_unknown");
                for(int n=0;n<20&&GetClipboardSequenceNumber()==before;n++){Forms.Application.DoEvents();Thread.Sleep(50);}
                copied=GetClipboardSequenceNumber();Require(copied!=before,"copy_not_observed");
                uint wanted,owner;GetWindowThreadProcessId(h,out wanted);GetWindowThreadProcessId(GetClipboardOwner(),out owner);
                Require(wanted==owner,"clipboard_owner_changed");
                Require(Forms.Clipboard.ContainsText(),"copied_text_unavailable");string text=Forms.Clipboard.GetText();
                Require(GetClipboardSequenceNumber()==copied,"clipboard_changed_during_read");
                stage="read_copy_verify";Thread.Sleep(150);Guard(h,Room,true);Require(text.Length<=8000,"copied_message_too_large");return LF(text);
            }finally{
                // Restore only the clipboard value this LINE copy just produced.
                if(copied!=0&&copied!=before&&GetClipboardSequenceNumber()==copied){
                    uint wanted,owner;GetWindowThreadProcessId(h,out wanted);GetWindowThreadProcessId(GetClipboardOwner(),out owner);
                    if(wanted==owner){if(original==null)Forms.Clipboard.Clear();else Forms.Clipboard.SetDataObject(saved,true);}
                }
            }
        }finally{EscapeMenu(h);}
    }
    static string RowId(AutomationElement row){
        var id=row.GetRuntimeId();Require(id!=null&&id.Length>0,"message_identity_missing");return String.Join(":",id.Select(v=>v.ToString()).ToArray());
    }
    static string[] VisibleRowIds(IntPtr h){
        var lists=Elements(Root(h,Room),ControlType.List);Require(lists.Count==1,"history_not_unique");
        return Elements(lists[0],ControlType.ListItem).Where(e=>!e.Current.IsOffscreen).Select(RowId).Distinct().ToArray();
    }
    static object ReadMessageOnce(bool baseline=false){
        var h=Find(Room);Require(h!=IntPtr.Zero,"test_room_not_open");Restore(h,Room);EmptyDraft(h);
        string lastId=RowId(LatestMessage(h));var visibleIds=VisibleRowIds(h);Require(visibleIds.Contains(lastId),"latest_identity_not_visible");
        if(baseline&&!RowOutgoing(LatestMessage(h).Current.BoundingRectangle))
            return new{status="message_baseline",room=Room,window_instance=Instance(h),input_empty_before_copy=true,last_message=(string)null,last_message_id=lastId,visible_message_ids=visibleIds,outgoing=false,send_invocations=0};
        string bound=Instance(h),text=ReadLastMessage(h);Require(Instance(h)==bound,"window_instance_changed");Require(RowId(LatestMessage(h))==lastId,"message_changed_during_copy");
        return new{status=baseline?"message_baseline":"message_read",room=Room,window_instance=bound,input_empty_before_copy=true,last_message=text,last_message_id=lastId,visible_message_ids=visibleIds,outgoing=lastReadOutgoing,send_invocations=0};
    }
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h,System.Text.StringBuilder name,int count);
    static void EscapeMenu(IntPtr h){
        uint wanted,actual;GetWindowThreadProcessId(h,out wanted);GetWindowThreadProcessId(GetForegroundWindow(),out actual);
        if(wanted!=actual)return;
        bool popup=false;EnumWindows((w,a)=>{uint owner;GetWindowThreadProcessId(w,out owner);if(owner==wanted&&IsWindowVisible(w)&&WindowClass(w)=="Qt663QWindowPopupSaveBits")popup=true;return true;},IntPtr.Zero);
        if(!popup)return;
        var keys=new[]{new SENDINPUT{type=1,value=new SENDUNION{keyboard=new KEYINPUT{vk=27}}},
                      new SENDINPUT{type=1,value=new SENDUNION{keyboard=new KEYINPUT{vk=27,flags=2}}}};
        SendKeys(2,keys,Marshal.SizeOf(typeof(SENDINPUT)));
    }
    static AutomationElement LatestMessage(IntPtr h){
        var root=Root(h,Room);var lists=Elements(root,ControlType.List);Require(lists.Count==1,"history_not_unique");
        var items=Elements(lists[0],ControlType.ListItem);Require(items.Count>0,"history_empty");
        var last=items.OrderBy(e=>e.Current.BoundingRectangle.Bottom).Last();var b=last.Current.BoundingRectangle;
        Require(lists[0].Current.BoundingRectangle.Contains(b),"latest_message_not_fully_visible");
        return last;
    }
    static bool RowOutgoing(System.Windows.Rect b){
        // The observed outgoing bubble is green. Unknown themes fail confirmation.
        int green=0,samples=0;
        using(var row=new Bitmap((int)b.Width,(int)b.Height)){
            using(var g=Graphics.FromImage(row))g.CopyFromScreen((int)b.Left,(int)b.Top,0,0,row.Size);
            for(int y=0;y<row.Height;y+=5)for(int x=row.Width/3;x<row.Width;x+=5){
                var c=row.GetPixel(x,y);samples++;if(Math.Abs(c.R-195)<=5&&Math.Abs(c.G-246)<=5&&Math.Abs(c.B-157)<=5)green++;
            }
        }
        return samples>0&&green>samples/10;
    }
    static void LastMessageMenu(IntPtr h){
        Guard(h,Room,true);EmptyDraft(h);
        var b=LatestMessage(h).Current.BoundingRectangle;
        lastReadOutgoing=RowOutgoing(b);
        var point=new POINT((int)b.Right-60,(int)(b.Top+b.Height/2));
        Require(GetAncestor(WindowFromPoint(point),2)==h,"message_obscured");
        // Right-click a point derived from the current last UIA item, not a fixed screen coordinate.
        Require(SetCursorPos(point.x,point.y),"cursor_move_failed");Guard(h,Room,true);
        var clicks=new[]{new INPUT{type=0,value=new INPUTUNION{mouse=new MOUSEINPUT{flags=8}}},
                         new INPUT{type=0,value=new INPUTUNION{mouse=new MOUSEINPUT{flags=16}}}};
        Require(SendInput(2,clicks,Marshal.SizeOf(typeof(INPUT)))==2,"context_menu_input_failed");
        Thread.Sleep(200);
    }
    static object InspectMessageMenu(){
        var h=Find(Room);Require(h!=IntPtr.Zero,"room_not_open");Restore(h,Room);
        try{
            LastMessageMenu(h);
            RECT r;Require(GetWindowRect(CopyPopup(h),out r),"bounds_missing");
            string path=System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Temp/line-menu-diagnostic.png");
            using(var bitmap=new Bitmap(r.right-r.left,r.bottom-r.top)){
                using(var g=Graphics.FromImage(bitmap))g.CopyFromScreen(r.left,r.top,0,0,bitmap.Size);
                bitmap.Save(path,System.Drawing.Imaging.ImageFormat.Png);
            }
            uint pid;GetWindowThreadProcessId(h,out pid);var windows=new List<object>();
            EnumWindows((w,a)=>{uint owner;GetWindowThreadProcessId(w,out owner);if(owner==pid&&IsWindowVisible(w)){RECT b;GetWindowRect(w,out b);var c=new System.Text.StringBuilder(256);GetClassName(w,c,256);windows.Add(new{hwnd=w.ToInt64(),title=Title(w),cls=c.ToString(),bounds=new[]{b.left,b.top,b.right,b.bottom}});}return true;},IntPtr.Zero);
            return new {status="menu_observed",capture=path,windows=windows};
        }
        finally{EscapeMenu(h);}
    }
}

