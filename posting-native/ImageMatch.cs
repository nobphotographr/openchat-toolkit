// Compare the current right-aligned thumbnail with the approved image.
// Low-information images and uncertain matches require manual review.
using System;using System.IO;using System.Drawing;using System.Collections.Generic;
using System.Web.Script.Serialization;
class ImageMatch {
 static Color SourceMean(Bitmap image,int x,int y,int rx,int ry){
  int r=0,g=0,b=0,n=0;
  for(int sy=Math.Max(0,y-ry);sy<=Math.Min(image.Height-1,y+ry);sy++)for(int sx=Math.Max(0,x-rx);sx<=Math.Min(image.Width-1,x+rx);sx++){
   var c=image.GetPixel(sx,sy);r+=c.R;g+=c.G;b+=c.B;n++;
  }
  return Color.FromArgb(r/n,g/n,b/n);
 }
 static int Mean(int[] integral,int pitch,int x,int y,int rx,int ry,int width,int height){
  int left=Math.Max(0,x-rx),right=Math.Min(width,x+rx+1),top=Math.Max(0,y-ry),bottom=Math.Min(height,y+ry+1);
  return (integral[bottom*pitch+right]-integral[top*pitch+right]-integral[bottom*pitch+left]+integral[top*pitch+left])/((right-left)*(bottom-top));
 }
 static int Main(string[] args){
  try{
   using(var source=new Bitmap(args[0]))using(var stream=new MemoryStream(Convert.FromBase64String(Console.ReadLine())))using(var row=new Bitmap(stream)){
    var reference=new List<Color>();var points=new List<PointF>();var colors=new HashSet<int>();
    for(int y=1;y<10;y++)for(int x=1;x<10;x++){
     float fx=x/10f,fy=y/10f;var c=SourceMean(source,(int)(fx*(source.Width-1)),(int)(fy*(source.Height-1)),Math.Max(1,source.Width/100),Math.Max(1,source.Height/100));
     reference.Add(c);points.Add(new PointF(fx,fy));colors.Add((c.R/32)*64+(c.G/32)*8+c.B/32);
    }
    if(colors.Count<8){Console.WriteLine("{\"matched\":false,\"reason\":\"low_visual_information\"}");return 0;}
    var pixelImage=new Bitmap(row.Width,row.Height,System.Drawing.Imaging.PixelFormat.Format32bppArgb);
    // UI screenshots can carry 144/192 DPI. Preserve pixels, not physical inches.
    using(var graphics=Graphics.FromImage(pixelImage))graphics.DrawImage(row,new Rectangle(0,0,row.Width,row.Height),0,0,row.Width,row.Height,GraphicsUnit.Pixel);
    var locked=pixelImage.LockBits(new Rectangle(0,0,row.Width,row.Height),System.Drawing.Imaging.ImageLockMode.ReadOnly,System.Drawing.Imaging.PixelFormat.Format32bppArgb);
    int stride=locked.Stride;var pixels=new byte[stride*row.Height];System.Runtime.InteropServices.Marshal.Copy(locked.Scan0,pixels,0,pixels.Length);pixelImage.UnlockBits(locked);pixelImage.Dispose();
    int pitch=row.Width+1;var red=new int[pitch*(row.Height+1)];var green=new int[red.Length];var blue=new int[red.Length];
    for(int y=0;y<row.Height;y++){int rs=0,gs=0,bs=0;for(int x=0;x<row.Width;x++){
     int offset=y*stride+x*4;rs+=pixels[offset+2];gs+=pixels[offset+1];bs+=pixels[offset];int i=(y+1)*pitch+x+1;
     red[i]=red[i-pitch]+rs;green[i]=green[i-pitch]+gs;blue[i]=blue[i-pitch]+bs;
    }}
    double best=1e9;int bx=0,by=0,bw=0,bh=0;double ratio=(double)source.Height/source.Width;
    for(int w=80;w<=Math.Min(row.Width*0.65,650);w+=2){
     int h=(int)Math.Round(w*ratio);if(h<40||h>row.Height)continue;
     for(int x=Math.Max((int)(row.Width*.35),row.Width-w-90);x<=row.Width-w;x+=2)for(int y=0;y<=row.Height-h;y+=2){
      double sum=0;int close=0;
      for(int i=0;i<points.Count;i++){
       int sx=x+(int)(points[i].X*(w-1)),sy=y+(int)(points[i].Y*(h-1)),rx=Math.Max(1,w/100),ry=Math.Max(1,h/100);
       var c=Color.FromArgb(Mean(red,pitch,sx,sy,rx,ry,row.Width,row.Height),Mean(green,pitch,sx,sy,rx,ry,row.Width,row.Height),Mean(blue,pitch,sx,sy,rx,ry,row.Width,row.Height));var expected=reference[i];
       int error=Math.Abs(c.R-expected.R)+Math.Abs(c.G-expected.G)+Math.Abs(c.B-expected.B);sum+=error;
       if(error<90)close++;if(i+1-close>4||sum>best*points.Count)break;
      }
      double mean=sum/points.Count;
      if(close>=points.Count*0.94&&mean<best){best=mean;bx=x;by=y;bw=w;bh=h;}
     }
    }
    Console.WriteLine(new JavaScriptSerializer().Serialize(new{matched=best<35,error=best,bounds=new[]{bx,by,bw,bh}}));return 0;
   }
  }catch{Console.WriteLine("{\"matched\":false,\"reason\":\"comparison_failed\"}");return 2;}
 }
}
