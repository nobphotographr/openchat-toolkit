using System;
using System.IO;
using System.Drawing;
using System.Drawing.Imaging;
using System.Web.Script.Serialization;

class ImageCodec {
    static int Main(string[] args){
        var json=new JavaScriptSerializer(){MaxJsonLength=30000000};
        try{
            byte[] bytes=Convert.FromBase64String(Console.ReadLine());
            if(bytes.Length==0||bytes.Length>10*1024*1024)throw new Exception("size");
            using(var stream=new MemoryStream(bytes))using(var source=Image.FromStream(stream,true,true)){
                if(source.RawFormat.Guid!=ImageFormat.Png.Guid&&source.RawFormat.Guid!=ImageFormat.Jpeg.Guid)throw new Exception("format");
                if(source.Width>8192||source.Height>8192||(long)source.Width*source.Height>24000000)throw new Exception("dimensions");
                // Respect JPEG orientation before removing all file metadata.
                if(Array.IndexOf(source.PropertyIdList,0x112)>=0){
                    int orientation=source.GetPropertyItem(0x112).Value[0];
                    var transforms=new[]{RotateFlipType.RotateNoneFlipNone,RotateFlipType.RotateNoneFlipNone,RotateFlipType.RotateNoneFlipX,RotateFlipType.Rotate180FlipNone,RotateFlipType.Rotate180FlipX,RotateFlipType.Rotate90FlipX,RotateFlipType.Rotate90FlipNone,RotateFlipType.Rotate270FlipX,RotateFlipType.Rotate270FlipNone};
                    if(orientation>=1&&orientation<=8)source.RotateFlip(transforms[orientation]);
                }
                using(var clean=new Bitmap(source.Width,source.Height,PixelFormat.Format24bppRgb)){
                    using(var g=Graphics.FromImage(clean)){g.Clear(Color.White);g.DrawImageUnscaled(source,0,0);}
                    using(var output=new MemoryStream()){
                        clean.Save(output,ImageFormat.Png);if(output.Length>20*1024*1024)throw new Exception("normalized_size");
                        Console.WriteLine(json.Serialize(new{ok=true,width=clean.Width,height=clean.Height,png=Convert.ToBase64String(output.ToArray())}));
                    }
                }
            }
            return 0;
        }catch{Console.WriteLine("{\"ok\":false}");return 2;}
    }
}
