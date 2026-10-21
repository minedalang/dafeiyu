#!/usr/bin/env python3
"""原创 Q 版蓝鲸的透明 WebM 试验素材，不依赖原项目的动画文件。

This is intentionally an *alternative illustration*, not a reconstruction of
shenshen's original character design. Requires Pillow and ffmpeg; output is
640x360 VP9 + alpha (yuva420p), 12 FPS, 4 s seamless loops.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import shutil
import subprocess
import argparse

from PIL import Image, ImageDraw, ImageFont

W, H, S, FPS, DUR = 640, 360, 2, 12, 4
NAVY = (37, 82, 128, 255)
BLUE = (85, 171, 219, 255)
BLUE_SHADOW = (55, 139, 194, 255)
CREAM = (227, 246, 252, 255)
ROSY = (250, 160, 172, 170)


def frame(scene: str, index: int, total: int = FPS*DUR) -> Image.Image:
    p = index / total * 2 * math.pi
    bob = 2.8 * math.sin(p)
    fin_rise = 9 * math.sin(p)
    im = Image.new('RGBA', (W*S, H*S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im, 'RGBA')

    def sc(box): return tuple(int(round(v*S)) for v in box)
    def el(box, fill, outline=None, width=1):
        d.ellipse(sc(box), fill=fill, outline=outline, width=width*S)
    def poly(points, fill, outline=None, width=1, joint='curve'):
        d.polygon([(int(x*S), int(y*S)) for x, y in points], fill=fill)
        if outline:
            pts = [(int(x*S), int(y*S)) for x,y in points]
            d.line(pts+[pts[0]], fill=outline, width=width*S, joint=joint)
    def line(points, fill, width=2):
        d.line([(int(x*S), int(y*S)) for x,y in points], fill=fill, width=width*S, joint='curve')
    def rr(box, r, fill, outline=None, width=1):
        d.rounded_rectangle(sc(box), radius=r*S, fill=fill, outline=outline, width=width*S)

    # Cast shadow and two-part tail; center frame is stable with 330px ground.
    el((252, 315, 447, 327), (33, 88, 139, 36))
    t = 7*math.sin(p+0.4)
    poly([(405,230+bob),(445,209+t),(473,194+t),(458,227+t),
          (484,247+t),(444,243+t),(408,257+bob)], BLUE_SHADOW, NAVY, 4)
    poly([(395,229+bob),(434,216+t),(450,226+t),(420,247+bob)], BLUE, None)

    # Body, soft whale tummy and dorsal fin
    poly([(324,183+bob),(347,155+bob),(363,189+bob)], BLUE_SHADOW, NAVY, 4)
    el((220,169+bob,432,319+bob), BLUE, NAVY, 4)
    el((244,238+bob,407,306+bob), CREAM)
    el((250,261+bob,377,302+bob), (240,250,255,185))

    # Cute eye and flush, with wink on the sip moment
    el((262,222+bob,286,244+bob), ROSY)
    el((293,212+bob,307,229+bob), NAVY)
    el((297,215+bob,301,220+bob), (255,255,255,255))
    el((312,169+bob,330,173+bob), NAVY)
    # mouth smile
    d.arc(sc((248,231+bob,282,259+bob)), start=7,end=134,fill=NAVY,width=3*S)
    # cheek highlight
    el((242,208+bob,250,218+bob), (255,255,255,112))

    # Scene-specific movable fins, objects and effects
    if scene == 'tea':
        poly([(370,254+bob),(394,246+bob+fin_rise*0.35),(413,267+bob),(393,279+bob)], BLUE_SHADOW, NAVY, 3)
        # little coaster
        el((378,293,447,306), (59, 117, 159, 100))
        rr((382,258+bob,430,294+bob),9, (255,248,235,255), NAVY, 3)
        el((424,265+bob,447,283+bob), (255,255,255,0), NAVY, 4)
        rr((386,259+bob,426,265+bob),4,(215,177,145,255))
        # steam drifting upward and back to origin for seamless loop
        for i in range(3):
            phase=(index/total*3 + i/3)%1
            y=248 - phase*65
            opacity=int(128*(1-phase)**1.5)
            d.arc(sc((393+i*12+2*math.sin(p+i),y-12,403+i*12+2*math.sin(p+i),y+8)),
                  80,290,fill=(81,139,187,opacity),width=2*S)
        for x,y in [(248,173),(460,170)]:
            el((x+4*math.sin(p),y+3*math.sin(p),x+5+4*math.sin(p),y+5+3*math.sin(p)),(158,219,248,130))
    elif scene == 'stretch':
        # Lift flippers gently in a continuous body stretch
        poly([(238,263+bob),(210,234+bob-fin_rise),(218,218+bob-fin_rise),
              (261,239+bob)], BLUE_SHADOW, NAVY, 3)
        poly([(373,251+bob),(417,208+bob-fin_rise),(432,214+bob-fin_rise),
              (401,282+bob)], BLUE_SHADOW, NAVY, 3)
        # stretch marks and sparkles
        for x,y,phase in [(201,194,0),(431,181,1),(470,244,2)]:
            size=3 + 2*(0.5+0.5*math.sin(p+phase))
            opacity=int(145+95*(0.5+0.5*math.sin(p+phase)))
            c=(255,198,98,opacity)
            line([(x-size,y),(x+size,y)],c,2)
            line([(x,y-size),(x,y+size)],c,2)
        # Add bright rising arc
        d.arc(sc((236,156+bob,422,305+bob)), 199,344,fill=(235,248,255,92),width=2*S)
    elif scene == 'read':
        poly([(366,259+bob),(398,267+bob),(393,278+bob),(368,287+bob)], BLUE_SHADOW, NAVY, 3)
        # open notebook with two leaves
        sway=2*math.sin(p)
        poly([(307,253+bob),(343,251+bob+sway),(352,257+bob),
              (357,299+bob),(342,295+bob),(309,300+bob)], (255,255,245,255), NAVY, 3)
        poly([(352,257+bob),(389,249+bob-sway),(418,258+bob),
              (414,299+bob),(383,295+bob),(357,299+bob)], (255,250,240,255), NAVY, 3)
        line([(354,257+bob),(357,299+bob)], NAVY,2)
        for dy in (0,9,18):
            line([(315,270+bob+dy),(342,268+bob+dy)],(108,162,187,190),1)
            line([(367,270+bob+dy),(405,268+bob+dy)],(108,162,187,190),1)
        # pomodoro timer as a little red tomato at the side
        el((433,261,462,288),(245,119,115,255),(136,60,87,255),2)
        poly([(439,262),(449,257),(454,262),(460,260),(452,268)],(75,153,113,255))
        line([(445,276),(451,276)],(255,255,255,220),2)
        line([(450,278),(451+5*math.sin(p),270+3*math.cos(p))],NAVY,2)
    return im.resize((W,H), Image.Resampling.LANCZOS)

SCENES = {
    'tea': '补充体力-喝热茶',
    'stretch': '恢复元气-轻轻伸展',
    'read': '番茄钟-专注读书',
}


def encode_scene(scene: str, output: Path):
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [shutil.which('ffmpeg') or 'ffmpeg', '-hide_banner', '-loglevel','error', '-y',
           '-f','rawvideo','-pix_fmt','rgba','-s',f'{W}x{H}','-r',str(FPS),'-i','-',
           '-an','-c:v','libvpx-vp9','-b:v','0','-crf','33','-speed','4',
           '-auto-alt-ref','0','-pix_fmt','yuva420p',str(output)]
    proc = subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        for i in range(FPS*DUR):
            proc.stdin.write(frame(scene,i).tobytes())
        proc.stdin.close()
        err=proc.stderr.read().decode('utf8','replace')
        code=proc.wait()
        if code:
            raise RuntimeError(f'ffmpeg exit {code}: {err[:2000]}')
    except Exception:
        proc.kill()
        proc.wait()
        output.unlink(missing_ok=True)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'experimental_webm')
    args=parser.parse_args()
    for scene,name in SCENES.items():
        file=args.output/(name+'.webm')
        encode_scene(scene,file)
        print(file, file.stat().st_size, 'bytes')
    im=Image.new('RGBA',(W*3,H), (236,245,250,255))
    for j,scene in enumerate(SCENES):
        im.paste(frame(scene,8+j*12),(j*W,0), frame(scene,8+j*12))
    im.convert('RGB').save(args.output/'PREVIEW.jpg',quality=91)

if __name__=='__main__':
    main()
