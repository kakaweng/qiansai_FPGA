"""Background read-only key snapshots; new physical presses select a route.

Connect/reconnect establishes a baseline and never replays a retained event.
Network reads never run on the OpenCV/UI thread.
"""
import json
import socket
import threading
import time


ACTIONS = {1:'turn_left', 2:'straight', 3:'turn_right'}


def read_snapshot(host='127.0.0.1',port=9016,total_timeout=.35,io_timeout=.2):
    """Read one complete snapshot with a deadline covering the whole request."""
    started=time.monotonic()
    with socket.create_connection((host,port),timeout=min(io_timeout,total_timeout)) as connection:
        raw=bytearray()
        while b'\n' not in raw:
            remaining=total_timeout-(time.monotonic()-started)
            if remaining<=0:raise OSError('late key snapshot')
            connection.settimeout(min(io_timeout,remaining))
            block=connection.recv(512)
            if not block:raise OSError('incomplete key snapshot')
            raw.extend(block)
            if len(raw)>1024:raise ValueError('oversized key snapshot')
    return json.loads(bytes(raw).decode('ascii')),time.monotonic()-started


class KeyEvents:
    def __init__(self):
        self.generation=None; self.previous=None

    def reset(self):
        self.generation=None; self.previous=None

    def update(self, packet):
        if (not isinstance(packet,dict) or type(packet.get('version')) is not int or packet['version']!=1
                or packet.get('valid') is not True or not isinstance(packet.get('generation'),str)
                or not 1<=len(packet['generation'])<=80
                or type(packet.get('event')) is not int or not 0<=packet['event']<=0xffffffff):
            self.reset();return None
        word=packet['event'];counter=word>>2;direction=word&3
        if packet['generation']!=self.generation or self.previous is None:
            self.generation=packet['generation'];self.previous=counter;return None
        before=self.previous;self.previous=counter
        # Small forward deltas only. Reset/backward snapshots resynchronize;
        # the RTL wraps its 30-bit counter to 1 instead of an ambiguous zero.
        advance=(counter-before)&0x3fffffff
        if not 0<advance<0x20000000 or not counter:return None
        return ACTIONS.get(direction)


class FPGAKeyClient(threading.Thread):
    def __init__(self,host='127.0.0.1',port=9016,report=None):
        super().__init__(daemon=True)
        self.host=host;self.port=port;self.finished=threading.Event()
        self.lock=threading.Lock();self.pending=None;self.events=KeyEvents()
        self.report=report;self.last_warning=-float('inf')

    def _report(self,message):
        if self.report is not None:self.report(message)

    def stop(self):
        self.finished.set()

    def take(self,now=None):
        now=time.monotonic() if now is None else now
        with self.lock:
            item=self.pending;self.pending=None
        if item and 0<=now-item[0]<=.5:return item[1]
        if item:self._report('FPGA_KEY_EXPIRED action=%s age_ms=%.0f'%(item[1],(now-item[0])*1000))
        return None

    def run(self):
        while not self.finished.is_set():
            try:
                packet,elapsed=read_snapshot(self.host,self.port)
                previous_generation=self.events.generation
                action=self.events.update(packet)
                if self.events.generation is not None and self.events.generation!=previous_generation:
                    self._report('FPGA_KEY_BASELINE event=%d sequence=%d rtt_ms=%.1f; press KEY5/6/7 now'%(
                        packet['event'],self.events.previous,elapsed*1000))
                if action:
                    with self.lock:self.pending=(time.monotonic(),action)
                    self._report('FPGA_KEY_EVENT event=%d action=%s rtt_ms=%.1f'%(packet['event'],action,elapsed*1000))
                elif self.events.generation is None:
                    with self.lock:self.pending=None
                    raise ValueError('invalid key packet')
            except (OSError,ValueError,UnicodeError) as error:
                self.events.reset()
                with self.lock:self.pending=None
                if time.monotonic()-self.last_warning>=5:
                    self.last_warning=time.monotonic()
                    self._report('FPGA_KEY_LINK_WAIT %s'%error)
            self.finished.wait(.075)


def watch(host='127.0.0.1',port=9016,seconds=20):
    """Independent read-only monitor; does not require the video/UI process."""
    events=KeyEvents();end=time.monotonic()+seconds
    last_report=0.;received=changes=0;previous=None
    print('FPGA_KEY_WATCH: press and release KEY5, KEY6, KEY7 separately; video preview may stay open',flush=True)
    while time.monotonic()<end:
        try:
            packet,elapsed=read_snapshot(host,port,total_timeout=2,io_timeout=2)
            action=events.update(packet)
            if events.generation is None:raise ValueError('invalid key packet')
            received+=1
            identity=(packet['generation'],packet['event'])
            if action:changes+=1
            if identity!=previous or time.monotonic()-last_report>=2:
                print('FPGA_KEY_WATCH event=%d sequence=%d direction=%s new=%s rtt_ms=%.1f'%(
                    packet['event'],packet['event']>>2,ACTIONS.get(packet['event']&3,'none'),
                    action or 'none',elapsed*1000),flush=True)
                last_report=time.monotonic();previous=identity
        except (OSError,ValueError,UnicodeError) as error:
            events.reset()
            print('FPGA_KEY_WATCH_WAIT %s'%error,flush=True)
        time.sleep(.2)
    print('FPGA_KEY_WATCH_DONE packets=%d new_events=%d'%(received,changes),flush=True)
    return received,changes


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Read-only FPGA route key monitor')
    parser.add_argument('--watch',action='store_true',help='watch physical key events without opening a preview')
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=9016)
    parser.add_argument('--seconds',type=int,default=20)
    arguments=parser.parse_args()
    if not 1<=arguments.port<=65535 or not 1<=arguments.seconds<=600:parser.error('invalid port or duration')
    try:
        received,_=watch(arguments.host,arguments.port,arguments.seconds)
    except KeyboardInterrupt:
        raise SystemExit(0)
    raise SystemExit(0 if received else 1)
