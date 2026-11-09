"""Local Windows video player: freeze a chosen frame, infer history, open rehearsal."""
import argparse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse

ROOT=Path(__file__).resolve().parent


class PlayerServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,video,port=0,profile='none'):
        self.video=Path(video).resolve()
        if not self.video.is_file() or self.video.suffix.lower()!='.mp4':raise ValueError('请选择本地 MP4 视频；浏览器需支持其视频编码。')
        self.profile=profile;self.token=secrets.token_urlsafe(24);self.job=None;self.lock=threading.Lock();self.process=None
        super().__init__(('127.0.0.1',port),Handler)

    def start_job(self,seconds):
        with self.lock:
            if self.job and self.job['status']=='working':return dict(self.job)
            name='freeze_'+time.strftime('%Y%m%d_%H%M%S')+'_'+secrets.token_hex(3)
            self.job=dict(id=name,status='working',at=seconds)
        threading.Thread(target=self.run_job,args=(name,seconds),daemon=True).start()
        return dict(self.job)

    def run_job(self,name,seconds):
        folder=ROOT/'rehearsal_sessions'/name;folder.parent.mkdir(exist_ok=True)
        command=[sys.executable,str(ROOT/'preview_pie_rehearsal.py'),'--video',str(self.video),'--at',str(seconds),
                 '--profile',self.profile,'--output',str(folder)]
        try:
            process=subprocess.Popen(command,cwd=str(ROOT),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.process=process;output,_=process.communicate(timeout=180)
            (folder.parent/(name+'.log')).write_bytes(output)
            if process.returncode:raise RuntimeError('生成未完成，详情见 rehearsal_sessions/'+name+'.log')
            with self.lock:self.job=dict(id=name,status='ready',at=seconds,url='/result/'+name)
        except BaseException as error:
            if self.process and self.process.poll() is None:self.process.kill();self.process.wait()
            with self.lock:self.job=dict(id=name,status='error',message=str(error))
        finally:self.process=None


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass

    def respond(self,data,status=200,kind='application/json; charset=utf-8'):
        body=json.dumps(data,ensure_ascii=False).encode() if isinstance(data,dict) else data
        self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers()
        try:self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass

    def do_GET(self):
        path=urlparse(self.path).path
        if path=='/':
            html=(ROOT/'rehearsal_player.html').read_text(encoding='utf-8').replace('__TOKEN__',self.server.token)
            self.respond(html.encode(),kind='text/html; charset=utf-8')
        elif path=='/status':
            with self.server.lock:status=dict(self.server.job or {'status':'idle'})
            self.respond(status)
        elif path=='/video':self.video()
        elif re.fullmatch('/result/freeze_[0-9]{8}_[0-9]{6}_[a-f0-9]{6}',path):
            file=ROOT/'rehearsal_sessions'/path.rsplit('/',1)[-1]/'index.html'
            if file.is_file():self.respond(file.read_bytes(),kind='text/html; charset=utf-8')
            else:self.respond({'error':'Not ready'},404)
        else:self.respond({'error':'Not found'},404)

    def video(self):
        file=self.server.video;size=file.stat().st_size;start,end=0,size-1;header=self.headers.get('Range')
        if header:
            match=re.fullmatch(r'bytes=(\d+)-(\d*)',header)
            if not match:self.respond({'error':'Unsupported range'},416);return
            start=int(match[1]);end=min(size-1,int(match[2])) if match[2] else size-1
            if start>end or start>=size:self.respond({'error':'Range outside file'},416);return
        self.send_response(206 if header else 200);self.send_header('Content-Type','video/mp4')
        self.send_header('Accept-Ranges','bytes');self.send_header('Content-Length',str(end-start+1))
        if header:self.send_header('Content-Range','bytes %d-%d/%d'%(start,end,size))
        self.end_headers()
        try:
            with file.open('rb') as f:
                f.seek(start);remaining=end-start+1
                while remaining:
                    chunk=f.read(min(256*1024,remaining))
                    if not chunk:break
                    self.wfile.write(chunk);remaining-=len(chunk)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass

    def do_POST(self):
        if self.path!='/freeze':self.respond({'error':'Not found'},404);return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<4096:raise ValueError('Invalid body length')
            payload=json.loads(self.rfile.read(length))
            if not secrets.compare_digest(str(payload.get('token','')),self.server.token):
                self.respond({'error':'Invalid session'},403);return
            seconds=float(payload['seconds'])
            if not 2<=seconds<24*3600:raise ValueError('请在视频 2 秒以后冻结，确保有足够观察历史。')
            self.respond(self.server.start_job(seconds))
        except (ValueError,KeyError,TypeError) as error:self.respond({'error':str(error)},400)


def main():
    p=argparse.ArgumentParser(description='在本机浏览器播放视频，按冻结按钮生成2.5D路线预演')
    p.add_argument('--video',type=Path,required=True);p.add_argument('--port',type=int,default=0)
    p.add_argument('--profile',choices=['none','pie'],default='none');p.add_argument('--no-open',action='store_true');a=p.parse_args()
    server=PlayerServer(a.video,a.port,a.profile);url='http://127.0.0.1:'+str(server.server_port)
    print('REHEARSAL_PLAYER_READY',url,flush=True)
    if not a.no_open:
        import webbrowser
        webbrowser.open(url)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        if server.process and server.process.poll() is None:server.process.terminate();server.process.wait()
        server.server_close()


if __name__=='__main__':main()
