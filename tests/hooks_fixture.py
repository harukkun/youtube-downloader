"""A deterministic clock + changing colours and tones; no network or LLM."""
import subprocess
from pathlib import Path

DIGITS = ['111101101101111','010110010010111','111001111100111','111001111001111','101101111001001','111100111001111','111100111101111','111001001001001','111101111101111','111101111001111']


def make_video(path):
    p = subprocess.Popen(['ffmpeg','-v','error','-y','-f','image2pipe','-vcodec','ppm','-r','30','-i','-',
        '-f','lavfi','-i','aevalsrc=sin(2*PI*(300*t+40*t*t)):s=16000:d=8',
        '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(path)],stdin=subprocess.PIPE)
    for frame in range(240):
        w,h=160,96
        rgb=bytearray(bytes((frame%200,40+(frame//30)*20,55))*w*h)
        label=f'{frame/30:04.1f}'
        for n,char in enumerate(label):
            bits=DIGITS[int(char)] if char.isdigit() else '000000000000010'
            for pos,bit in enumerate(bits):
                if bit=='1':
                    for y in range(5):
                        for x in range(5):
                            off=((30+(pos//3)*5+y)*w+30+n*22+(pos%3)*5+x)*3
                            rgb[off:off+3]=b'\xff\xff\xff'
        p.stdin.write(f'P6\n{w} {h}\n255\n'.encode()+rgb)
    p.stdin.close()
    if p.wait()!=0: raise RuntimeError('fixture video failed')

SRT = '''1
00:00:01,000 --> 00:00:02,400
진짜 맛있어요

2
00:00:02,000 --> 00:00:03,500
꼭 만들어 보세요

3
00:00:04,000 --> 00:00:05,000
설탕을 넣으세요
'''
