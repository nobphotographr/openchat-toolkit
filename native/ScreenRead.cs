// Screen-only history inspection. Never invokes LINE's export command.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Automation;
using Forms=System.Windows.Forms;

public partial class LineWindowPrep {
    static string screenDiagnostic;
    [DllImport("user32.dll")] static extern uint GetClipboardSequenceNumber();
    [DllImport("user32.dll")] static extern IntPtr GetClipboardOwner();
    static string CopyRow(IntPtr room,AutomationElement row){
        try{
            var popup=RowMenu(room,row);POINT p;RECT rect;GetWindowRect(popup,out rect);
            using(var image=Capture(popup)){
                bool imageMenu=false;
                try{VerifiedLabelPoint(image,Path.Combine(Folder,"native/image-recognition-label-150.png"),"27D9A734DB945459CA3FBFCF710C2806C3F4ED8C65E0CD5DE35CC182BF316709",100,34);imageMenu=true;}
                catch(InvalidOperationException e){if(!e.Message.StartsWith("copy_visual_label_not_unique"))throw;}
                if(imageMenu)throw new InvalidOperationException("image_attachment");
                try{p=CopyLabelPoint(image);}
                catch(InvalidOperationException copyError){
                    if(copyError.Message=="copy_visual_label_not_unique_0"){
                        RECT window;GetWindowRect(room,out window);
                        var bounds=row.Current.BoundingRectangle;
                        using(var context=Capture(room)){
                            var clipped=Rectangle.Intersect(new Rectangle((int)bounds.Left-window.left,(int)bounds.Top-window.top,(int)bounds.Width,(int)bounds.Height),new Rectangle(0,0,context.Width,context.Height));
                            if(clipped.Width>0&&clipped.Height>0)
                                using(var rowImage=context.Clone(clipped,context.PixelFormat))
                                    if(IsVideoAttachment(image,rowImage))throw new InvalidOperationException("video_attachment");
                        }
                    }
                    if(screenDiagnostic==null){
                        using(var stream=new MemoryStream()){image.Save(stream,ImageFormat.Png);screenDiagnostic=Convert.ToBase64String(stream.ToArray());}
                    }
                    throw;
                }
            }
            p.x+=rect.left;p.y+=rect.top;
            stage="clipboard_snapshot";uint before=GetClipboardSequenceNumber(),copied=0;
            var original=Forms.Clipboard.GetDataObject();var saved=new Forms.DataObject();
            if(original!=null)foreach(string format in original.GetFormats(false))saved.SetData(format,false,original.GetData(format,false));
            Require(GetClipboardSequenceNumber()==before,"clipboard_changed_before_copy");
            try{
                stage="clipboard_copy";
                Require(Popups(room).Contains(popup),"copy_menu_changed");
                Click(room,Room,p.x,p.y,true);
                for(int i=0;i<60&&GetClipboardSequenceNumber()==before;i++){Forms.Application.DoEvents();Thread.Sleep(50);}
                copied=GetClipboardSequenceNumber();
                if(copied==before){
                    using(var image=Capture(room))using(var stream=new MemoryStream()){image.Save(stream,ImageFormat.Png);screenDiagnostic=Convert.ToBase64String(stream.ToArray());}
                    throw new InvalidOperationException("copy_not_observed:menus="+Popups(room).Count+";point="+p.x+","+p.y);
                }
                Require(PID(GetClipboardOwner())==PID(room),"clipboard_owner_changed");
                Require(Forms.Clipboard.ContainsText(),"nontext_clipboard");
                var text=Forms.Clipboard.GetText().Replace("\r\n","\n").Replace("\r","\n");
                Require(text.Length>0&&text.Length<100000&&text.IndexOf('\0')<0,"copied_text_invalid");
                Require(GetClipboardSequenceNumber()==copied,"clipboard_changed_during_read");
                return text;
            }finally{
                uint current=GetClipboardSequenceNumber();
                if(copied!=0&&copied!=before&&current==copied&&PID(GetClipboardOwner())==PID(room)){
                    stage="clipboard_restore";Forms.Application.DoEvents();
                    if(original==null)Forms.Clipboard.Clear();else Forms.Clipboard.SetDataObject(saved,true);
                }
            }
        }finally{CloseReadMenu(room);}
    }
    static string Digest(string text){using(var sha=System.Security.Cryptography.SHA256.Create())return BitConverter.ToString(sha.ComputeHash(System.Text.Encoding.UTF8.GetBytes(text))).Replace("-","").ToLowerInvariant();}
    static void WheelHistory(IntPtr room,int delta){
        Guard(room,Room,true);RequireDraftIsEmpty(room);
        var b=History(room).Current.BoundingRectangle;var p=new POINT((int)(b.Left+b.Width/2),(int)(b.Top+b.Height/2));
        Require(GetAncestor(WindowFromPoint(p),2)==room,"history_obscured");Require(SetCursorPos(p.x,p.y),"pointer_failed");
        var input=new[]{new INPUT{type=0,value=new INPUTUNION{mouse=new MOUSEINPUT{flags=0x0800,mouseData=unchecked((uint)delta)}}}};
        Require(SendInput(1,input,Marshal.SizeOf(typeof(INPUT)))==1,"scroll_failed");Thread.Sleep(300);
        Guard(room,Room,true);
    }
    static string RowShape(IntPtr room){return String.Join(";",Elements(History(room),ControlType.ListItem).Select(r=>String.Join(",",r.GetRuntimeId())+":"+r.Current.BoundingRectangle.ToString()));}
    static object ReadScreen(IntPtr room,string anchorsValue,int startStep){
        Require(startStep>=0&&startStep<=120,"screen_resume_step_invalid");
        var anchors=String.IsNullOrEmpty(anchorsValue)?new string[0]:anchorsValue.Split(',');
        Require(anchors.Length<=3&&anchors.All(a=>a.Length==64&&a.All(c=>"0123456789abcdef".Contains(c))),"screen_anchors_invalid");
        stage="screen_bottom";Thread.Sleep(500);
        string previous=null;bool bottom=false;
        for(int i=0;i<8;i++){WheelHistory(room,-12000);string shape=RowShape(room);if(shape==previous){bottom=true;break;}previous=shape;}
        Require(bottom,"history_bottom_unconfirmed");
        for(int seek=0;seek<startStep;seek++){stage="screen_resume_seek";WheelHistory(room,600);}
        int scrollStep=startStep,nextStep=startStep;bool reachedEnd=false,anchored=false;
        var pages=new List<object>();int copied=0,skipped=0;var reasons=new Dictionary<string,int>();
        var scanHashes=new List<string>();var scanIds=new Dictionary<string,string>();
        var watch=Stopwatch.StartNew();bool bounded=false;
        for(int page=0;page<6;page++){
            stage="screen_copy_rows";var list=History(room);var area=list.Current.BoundingRectangle;
            var rows=Elements(list,ControlType.ListItem).OrderByDescending(r=>r.Current.BoundingRectangle.Bottom).ToList();
            var messages=new List<object>();var pageHashes=new List<string>();string shape=RowShape(room);var diagnostics=new List<object>();
            foreach(var row in rows){
                if(watch.ElapsedMilliseconds>36000||copied>=45){bounded=true;break;}
                var b=row.Current.BoundingRectangle;
                if(b.Bottom<area.Top+150||b.Top>area.Bottom-25)continue;
                try{
                    string text=CopyRow(room,row);
                    string hash=Digest(text);
                    string itemId=String.Join(",",row.GetRuntimeId());
                    if(scanIds.ContainsKey(itemId))Require(scanIds[itemId]==hash,"screen_row_changed_during_scan");
                    else{scanIds[itemId]=hash;scanHashes.Add(hash);}
                    messages.Add(new{body=text,sha256=hash,item_id=itemId});pageHashes.Add(hash);copied++;
                    diagnostics.Add(new{id=String.Join(",",row.GetRuntimeId()),height=b.Height,reason="copied",length=text.Length});
                }catch(InvalidOperationException e){
                    if(e.Message=="no_text_bubble"||e.Message=="row_not_visible"||e.Message=="row_menu_missing"||e.Message=="nontext_clipboard"||e.Message=="image_attachment"||e.Message=="video_attachment"||e.Message.StartsWith("copy_not_observed")||e.Message.StartsWith("copy_visual_label_not_unique")){
                        skipped++;string reason=e.Message.Split(':')[0];if(!reasons.ContainsKey(reason))reasons[reason]=0;reasons[reason]++;
                        diagnostics.Add(new{id=String.Join(",",row.GetRuntimeId()),height=b.Height,reason=reason,length=0});continue;}
                    throw;
                }
            }
            pages.Add(new{messages=messages,diagnostics=diagnostics});
            nextStep=pageHashes.Count>=3?scrollStep:Math.Max(startStep,scrollStep-1);
            anchored=anchors.Length>0&&Enumerable.Range(0,Math.Max(0,scanHashes.Count-anchors.Length+1)).Any(i=>scanHashes.Skip(i).Take(anchors.Length).SequenceEqual(anchors));
            if(anchored)break;
            if(bounded||page==5||scrollStep>=120)break;
            stage="screen_scroll_up";WheelHistory(room,600);
            if(RowShape(room)==shape){reachedEnd=true;break;}
            scrollStep++;
        }
        Require(copied>0,"no_copyable_text_messages");
        Guard(room,Room,true);RequireDraftIsEmpty(room);
        return new{schema_version=1,status="screen_read",room=Room,observed_at=DateTimeOffset.UtcNow.ToString("o"),
            pages=pages,skipped_rows=skipped,skip_reasons=reasons,bounded=bounded,bottom_confirmed=true,send_invocations=0,history_download_invocations=0,
            start_scroll_steps=startStep,next_scroll_steps=nextStep,has_more=!anchored&&!reachedEnd};
    }
    static bool SamePixel(Color a,Color b){return Math.Abs(a.R-b.R)<=8&&Math.Abs(a.G-b.G)<=8&&Math.Abs(a.B-b.B)<=8;}
    // Reference is the visually reviewed Japanese Copy label, not a menu ordinal.
    // Unique full-pixel match; a new DPI/theme/font fails closed rather than clicking.
    static POINT CopyLabelPoint(Bitmap bitmap){
        return VerifiedLabelPoint(bitmap,Path.Combine(Folder,"native/copy-label-150.png"),"37A88EF330991E6610D5FA1224D5B370D5C44BEC8889193D5E0FC6957D4402A1",55,34);
    }
    // A missing Copy entry alone is never evidence that a row is non-text.
    // Require both the reviewed Save-as label and the video placeholder inside
    // this exact UIA row. Unknown menus, DPI or themes remain read failures.
    static bool IsVideoAttachment(Bitmap menu,Bitmap rowImage){
        try{
            VerifiedLabelPoint(menu,Path.Combine(Folder,"native/save-attachment-label-150.png"),"47F6827D617A1CF51D669644FC7C8C957000157D74F1311AC302A5F86E71840C",154,34);
            VerifiedLabelPoint(rowImage,Path.Combine(Folder,"native/video-placeholder-150.png"),"28893D6C233290861AF28EDF9CD46702FA5F9A8C9B537D79B50516090493835B",51,52,245);
            return true;
        }catch(InvalidOperationException e){
            if(e.Message.StartsWith("copy_visual_label_not_unique"))return false;
            throw;
        }
    }
    static POINT VerifiedLabelPoint(Bitmap bitmap,string reference,string hashValue,int width,int height,int inkThreshold=150){
        using(var hash=System.Security.Cryptography.SHA256.Create())
            Require(BitConverter.ToString(hash.ComputeHash(System.IO.File.ReadAllBytes(reference))).Replace("-","")
                    ==hashValue,"copy_reference_changed");
        using(var label=new Bitmap(reference)){
            Require(label.Width==width&&label.Height==height,"copy_reference_invalid");
            var ink=new List<POINT>();for(int y=0;y<label.Height;y++)for(int x=0;x<label.Width;x++)
                if(label.GetPixel(x,y).R<inkThreshold)ink.Add(new POINT(x,y));
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

    static AutomationElement History(IntPtr room){
        var lists=Elements(Root(room,Room),ControlType.List);Require(lists.Count==1,"history_not_unique");return lists[0];
    }
    static void CloseReadMenu(IntPtr room){
        foreach(var popup in Popups(room))PostMessage(popup,16,IntPtr.Zero,IntPtr.Zero);
        Thread.Sleep(80);
    }
    static IntPtr RowMenu(IntPtr room,AutomationElement row){
        Guard(room,Room,true);RequireDraftIsEmpty(room);Require(Popups(room).Count==0,"existing_menu_preserved");
        var history=History(room).Current.BoundingRectangle;
        // LINE's announcement overlay can cover the top of the list without
        // changing its UIA bounds. Never target that covered strip.
        history.Y+=125;history.Height-=125;
        var rect=row.Current.BoundingRectangle;rect.Intersect(history);
        Require(rect.Width>100&&rect.Height>25,"row_not_visible");
        RECT window;GetWindowRect(room,out window);POINT point=new POINT();bool found=false;
        using(var bitmap=Capture(room)){
            int best=0;
            for(int yy=(int)rect.Top+15;yy<rect.Bottom-(rect.Height>500?130:35);yy+=20){
                int start=-1;
                for(int xx=(int)rect.Left+65;xx<rect.Right-25;xx++){
                    var c=bitmap.GetPixel(xx-window.left,yy-window.top);bool white=(c.R>249&&c.G>249&&c.B>249)||(Math.Abs(c.R-195)<=5&&Math.Abs(c.G-246)<=5&&Math.Abs(c.B-157)<=5);
                    if(white&&start<0)start=xx;
                    if((!white||xx==(int)rect.Right-26)&&start>=0){
                        int length=xx-start;
                        if(length>=35){best=length;point=new POINT(start+length/2,yy);found=true;}start=-1;
                    }
                }
            }
            Require(found&&best>=35,"no_text_bubble");
        }
        Require(row.Current.BoundingRectangle.IntersectsWith(rect),"row_changed");
        Require(GetAncestor(WindowFromPoint(point),2)==room,"row_obscured");
        Require(SetCursorPos(point.x,point.y),"pointer_failed");Guard(room,Room,true);
        var input=new[]{new INPUT{type=0,value=new INPUTUNION{mouse=new MOUSEINPUT{flags=8}}},new INPUT{type=0,value=new INPUTUNION{mouse=new MOUSEINPUT{flags=16}}}};
        Require(SendInput(2,input,Marshal.SizeOf(typeof(INPUT)))==2,"context_click_failed");
        for(int i=0;i<10;i++){Thread.Sleep(80);var menus=Popups(room);if(menus.Count==1)return menus[0];Require(menus.Count==0,"multiple_menus");}
        throw new InvalidOperationException("row_menu_missing");
    }
    static object InspectCopyMenu(IntPtr room){
        Thread.Sleep(700);var rows=Elements(History(room),ControlType.ListItem);
        Require(rows.Count>0,"history_empty");
        try{
            var popup=RowMenu(room,rows.OrderBy(r=>r.Current.BoundingRectangle.Bottom).Last());
            string png;using(var b=Capture(popup))using(var s=new MemoryStream()){b.Save(s,ImageFormat.Png);png=Convert.ToBase64String(s.ToArray());}
            return new{status="copy_menu_inspected",room=Room,png=png,send_invocations=0};
        }finally{CloseReadMenu(room);}
    }
    static object InspectHistory(IntPtr room){
        Thread.Sleep(800);Guard(room,Room,true);RequireDraftIsEmpty(room);
        var root=Root(room,Room);
        var all=root.FindAll(TreeScope.Descendants,Condition.TrueCondition).Cast<AutomationElement>().ToList();
        Require(all.Count<2000,"unexpected_history_tree");
        var rows=all.Select(e=>{
            object p;string text=null;
            if(e.Current.ControlType!=ControlType.Edit&&e.TryGetCurrentPattern(TextPattern.Pattern,out p))text=((TextPattern)p).DocumentRange.GetText(30000);
            if(e.Current.ControlType!=ControlType.Edit&&String.IsNullOrEmpty(text)&&e.TryGetCurrentPattern(ValuePattern.Pattern,out p))text=((ValuePattern)p).Current.Value;
            var b=e.Current.BoundingRectangle;
            return new {name=e.Current.Name,cls=e.Current.ClassName,type=e.Current.ControlType.ProgrammaticName,offscreen=e.Current.IsOffscreen,
                bounds=new[]{b.X,b.Y,b.Width,b.Height},patterns=e.GetSupportedPatterns().Select(x=>x.ProgrammaticName).ToArray(),text=text};
        }).ToArray();
        string png;using(var b=Capture(room))using(var s=new MemoryStream()){b.Save(s,ImageFormat.Png);png=Convert.ToBase64String(s.ToArray());}
        return new {status="history_inspected",room=Room,elements=rows,png=png,send_invocations=0};
    }
}
