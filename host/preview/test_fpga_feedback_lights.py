"""只检查实体灯接线/极性，不代表识别或驾驶规则通过。先关闭预览窗口。"""
import argparse
import time
from fpga_drive_feedback import FPGAFeedbackClient,OFF,PENDING,GO,STOP,YIELD,UNKNOWN,ALARM

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=9017)
    parser.add_argument('--active-low',action='store_true')
    args=parser.parse_args()
    client=FPGAFeedbackClient(args.host,args.port,args.active_low)
    sequence=((GO,1,1.2,'A2 左灯'),(GO,2,1.2,'B3 直行灯'),(GO,3,1.2,'C5 右灯'),
              (STOP,0,1,'三灯常亮/停车'),(PENDING,1,1.3,'左灯慢闪/请求'),
              (YIELD,0,2,'三灯双闪/礼让'),(UNKNOWN,0,1.5,'三灯慢闪/未知'),
              (ALARM,0,2,'三灯快闪/报警'))
    print('仅灯光测试：不读取视频、不执行模型、不改变驾驶规则。',flush=True)
    try:
        for mode,direction,seconds,label in sequence:
            print(label,flush=True)
            until=time.monotonic()+seconds
            while time.monotonic()<until:
                client.send((mode,direction,False,'manual_light_test'))
                time.sleep(.2)
    finally:
        client.send((OFF,0,False,'test_end'))
    print('FPGA_FEEDBACK_LIGHT_COMMANDS_PASS；还需目视核对实体灯。',flush=True)

if __name__=='__main__':main()
