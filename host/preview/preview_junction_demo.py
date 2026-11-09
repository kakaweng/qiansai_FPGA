"""Windows offline scene demonstration using the real C controller and synthetic evidence.

It never connects to the board. Keys 1/2/3/0 set constructed red/yellow/green/unknown
input. The normal junction controls use the same HTTP API as the Ubuntu preview.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from urllib.request import Request, build_opener, ProxyHandler

ROOT=Path(__file__).resolve().parent


def board_clock():
    if os.name=='nt':
        ctypes.windll.kernel32.GetTickCount64.restype=ctypes.c_ulonglong
        return ctypes.windll.kernel32.GetTickCount64()
    return int(time.monotonic()*1000)


class DemoController:
    """Private ephemeral localhost controller; no hard-coded production ports or files."""
    def __init__(self,executable):
        self.executable=Path(executable).resolve()
        self.color=1
        self.multiple=False
        self.people=0
        self.feeding=True
        self.stop_event=threading.Event()
        self.process=self.worker=None
        self.opener=build_opener(ProxyHandler({}))

    def __enter__(self):
        if not self.executable.is_file(): raise FileNotFoundError(self.executable)
        with socket.socket() as tcp,socket.socket(type=socket.SOCK_DGRAM) as udp:
            tcp.bind(('127.0.0.1',0)); udp.bind(('127.0.0.1',0))
            self.port=tcp.getsockname()[1]; self.report_port=udp.getsockname()[1]
        self.url='http://127.0.0.1:%d'%self.port
        self.process=subprocess.Popen([str(self.executable),'--port',str(self.port),'--report-port',str(self.report_port)],
                                      stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            self.wait(lambda s:s['state']=='IDLE')
            self.worker=threading.Thread(target=self._evidence,daemon=True); self.worker.start()
            return self
        except BaseException:
            self.__exit__(None,None,None)
            raise

    def request(self,route,post=False):
        req=Request(self.url+route,data=b'{}' if post else None,headers={'X-Sync-Control':'1'})
        with self.opener.open(req,timeout=1) as response: return json.load(response)

    def wait(self,predicate,timeout=6):
        deadline=time.monotonic()+timeout
        latest=None
        while time.monotonic()<deadline:
            try:
                latest=self.request('/api/status')
                if predicate(latest): return latest
            except OSError: pass
            if self.process.poll() is not None: raise RuntimeError('Offline controller exited')
            time.sleep(.03)
        raise TimeoutError('Controller condition not met: %r'%latest)

    def action(self,name): return self.request('/api/drive?action='+name,True)

    def _evidence(self):
        with socket.socket(type=socket.SOCK_DGRAM) as udp:
            while not self.stop_event.is_set():
                if self.feeding:
                    now=board_clock()
                    udp.sendto(('FRONT %d 1 2 %d 0 1 %d %d'%(now,now,self.people,now)).encode(),('127.0.0.1',self.report_port))
                    boxes='4500 1500 5000 2500 %d'%self.color
                    if self.multiple: boxes+=' 6500 1500 7000 2500 3'
                    udp.sendto(('LIGHTS %d %d %d %s'%(now,now,2 if self.multiple else 1,boxes)).encode(),
                               ('127.0.0.1',self.report_port))
                self.stop_event.wait(.1)

    def __exit__(self,*args):
        self.stop_event.set()
        if self.worker: self.worker.join(2)
        if self.process:
            self.process.terminate()
            try: self.process.wait(timeout=2)
            except subprocess.TimeoutExpired: self.process.kill(); self.process.wait(timeout=2)
            self.process.stderr.close()


def synthetic_video(color=1,multiple=False):
    import cv2
    import numpy as np
    image=np.zeros((480,1280,3),np.uint8)
    road=image[:,213:1066]
    road[:220]=(54,41,27); road[220:]=(66,57,44)
    cv2.fillPoly(road,[np.array([(380,220),(480,220),(853,480),(0,480)])],(75,65,48))
    cv2.line(road,(398,220),(150,480),(227,221,202),5,cv2.LINE_AA)
    for y in range(260,480,45):
        cv2.line(road,(480+(y-220),y),(480+(y-200),y+20),(227,221,202),4,cv2.LINE_AA)
    cv2.line(road,(270,360),(620,360),(227,221,202),6,cv2.LINE_AA)
    colors=((110,110,110),(82,90,255),(49,197,255),(138,230,46))
    for nx,c in ((4500,color),(6500,3)) if multiple else ((4500,color),):
        a=(round(nx*853/10000),72); b=(round((nx+500)*853/10000),120)
        cv2.rectangle(road,a,b,(18,13,9),-1)
        cv2.circle(road,((a[0]+b[0])//2,96),11,colors[c],-1,cv2.LINE_AA)
        cv2.rectangle(road,a,b,(0,220,255),2)
        cv2.putText(road,'LIGHT',(a[0],65),cv2.FONT_HERSHEY_SIMPLEX,.45,(0,220,255),1,cv2.LINE_AA)
    cv2.putText(road,'OFFLINE - SYNTHETIC INPUT',(20,445),cv2.FONT_HERSHEY_SIMPLEX,.65,(100,180,255),1,cv2.LINE_AA)
    return image


def main():
    import cv2
    from front_junction_preview import JunctionClient,JunctionDashboard
    parser=argparse.ArgumentParser(description='2.5D路口离线交互演示（构造灯色，无实机推理）')
    parser.add_argument('--controller',type=Path,default=ROOT/'rk3568_front_p1_deploy/host/rk_front_p1_control.exe')
    args=parser.parse_args()
    with DemoController(args.controller) as demo:
        demo.request('/api/start',True)
        demo.wait(lambda s:s['state']=='RUNNING' and s['drive']['p1']['matches']==1)
        demo.action('scene_open')
        client=JunctionClient(demo.url); panel=JunctionDashboard(client); client.start()
        title='OFFLINE Junction Demo - Synthetic Signals'
        cv2.namedWindow(title,cv2.WINDOW_NORMAL); cv2.resizeWindow(title,*panel.WINDOW_SIZE)
        cv2.setMouseCallback(title,panel.click)
        print('OFFLINE DEMO: 1=red 2=yellow 3=green 0=unknown. G=start B=pause R=reset F=violation M=mode Q=exit.')
        try:
            while True:
                frame=panel.compose(synthetic_video(demo.color,demo.multiple),True)
                # The offline marker is always visible in the header, regardless of mode.
                patch=panel._patch('离线构造状态 · 无实机推理',16,'#fff7ed','#9a3412')
                frame[8:8+patch.shape[0],640:640+patch.shape[1]]=patch
                cv2.imshow(title,frame)
                key=cv2.waitKey(30)&0xff
                if key in (27,ord('q'),ord('Q')): break
                if key in (ord('s'),ord('S')):
                    folder=ROOT/'ui_preview_junction'; folder.mkdir(exist_ok=True)
                    path=folder/('offline_snapshot_%d.png'%time.time_ns())
                    if not cv2.imwrite(str(path),frame): raise OSError('Cannot save offline screenshot')
                    print(path)
                elif key in map(ord,'0123'): demo.color=int(chr(key))
                else: panel.handle_key(key)
                if cv2.getWindowProperty(title,cv2.WND_PROP_VISIBLE)<1: break
        finally:
            client.stop(); client.join(2); cv2.destroyAllWindows()


if __name__=='__main__': main()
