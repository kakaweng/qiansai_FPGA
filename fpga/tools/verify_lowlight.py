"""Generate behavioral/real-frame vectors, simulate RTL and compare integer results."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import cv2
import numpy as np
from lowlight_reference import enhance,TABLES

ROOT=Path(__file__).resolve().parents[1]


def write_frame(stream,image,strength,next_strength=None):
    output=enhance(image,strength)
    def emit(vs,hs,de,rgb=(0,0,0),result=(0,0,0),control=None):
        control=strength if control is None else control
        inp=(control<<27)|(vs<<26)|(hs<<25)|(de<<24)|(int(rgb[0])<<16)|(int(rgb[1])<<8)|int(rgb[2])
        out=(vs<<26)|(hs<<25)|(de<<24)|(int(result[0])<<16)|(int(result[1])<<8)|int(result[2])
        stream.write('%08x %07x\n'%(inp,out))
    for _ in range(12):emit(0,0,0)
    for _ in range(3):emit(1,1,0)
    for _ in range(12):emit(0,0,0)
    for y,(line,result) in enumerate(zip(image,output)):
        ctl=next_strength if next_strength is not None and y>=len(image)//2 else strength
        for rgb,pixel in zip(line,result):emit(0,0,1,rgb,pixel,ctl)
        for _ in range(8):emit(0,1,0,control=ctl)
    for _ in range(12):emit(0,0,0)


def verify_properties():
    for strength,table in enumerate(TABLES):
        target=table['target']
        assert all(a<=b for a,b in zip(target,target[1:]))
        assert all(value>=index for index,value in enumerate(target))
        assert target[0]==0 and target[255]==255
    rng=np.random.default_rng(42)
    random=rng.integers(0,256,(100,256,3),dtype=np.uint8)
    for level in range(3):
        result=enhance(random,level)
        assert np.all(result.max(axis=2).astype(np.int32)>=random.max(axis=2).astype(np.int32)-12)
    # Exact neutral ramps preserve order; isolated sharp edges are not blurred.
    ramp=np.repeat(np.arange(256,dtype=np.uint8)[:,None,None],4,axis=1)
    ramp=np.repeat(ramp,3,axis=2)
    edge=np.zeros((1,12,3),dtype=np.uint8);edge[:,6:]=220
    for level in range(3):
        assert np.array_equal(enhance(ramp,level)[:,0,0],TABLES[level]['target'])
        sharp=enhance(edge,level)
        assert np.all(sharp[:,:6]==0) and np.all(sharp[:,6:]==TABLES[level]['target'][220])
    flat=np.full((32,512,3),24,dtype=np.int32)+rng.integers(-4,5,(32,512,3))
    filtered=enhance(flat.astype(np.uint8),1).astype(np.float32)
    gain=np.array(TABLES[1]['gain_q8'])[flat.max(axis=2)]
    unfiltered=((flat*gain[:,:,None]+128)>>8).astype(np.float32)
    ratio=float(filtered[:,2:-2].std()/unfiltered[:,2:-2].std())
    assert ratio<.8,(ratio,'noise variance reduction absent')
    return dict(monotonic_curves=True,black_white_endpoints=True,synthetic_flat_noise_std_ratio=ratio,
                note='Synthetic noise check only; no claim of ground-truth image restoration')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--iverilog',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=ROOT.parent/'lowlight_build_final_20261102')
    args=parser.parse_args();args.output.mkdir(exist_ok=True)
    source=ROOT.parent/'09_pcie_test/pcie_host/demo_cases_20261102/06_night_enhancement/front.mp4'
    capture=cv2.VideoCapture(str(source));capture.set(cv2.CAP_PROP_POS_MSEC,1000)
    ok,bgr=capture.read();capture.release()
    if not ok:raise SystemExit('Night verification source unavailable')
    real=cv2.cvtColor(cv2.resize(bgr,(320,180),interpolation=cv2.INTER_AREA),cv2.COLOR_BGR2RGB)
    ramp=np.repeat(np.arange(256,dtype=np.uint8)[:,None,None],8,axis=1)
    ramp=np.repeat(ramp,3,axis=2)
    noise=np.random.default_rng(91).integers(0,256,(48,192,3),dtype=np.uint8)
    vectors=args.output/'lowlight_vectors.txt'
    with vectors.open('w',encoding='ascii') as stream:
        for strength in range(3):
            write_frame(stream,ramp,strength)
            write_frame(stream,noise,strength)
            # Changing control mid-frame must not change that frame's curve.
            write_frame(stream,real,strength,next_strength=(strength+1)%3)
        stream.write('00000000 0000000\n'*14)
    env=dict(os.environ);env['PATH']=str(args.iverilog)+';D:/Git/mingw64/bin;'+env.get('PATH','')
    binary=args.output/'lowlight.vvp'
    commands=[[str(args.iverilog/'iverilog.exe'),'-g2012','-s','lowlight_v2_tb','-o',str(binary),
               str(ROOT/'sim/lowlight_v2_tb.v'),str(ROOT/'src/hdmi_lowlight_v2.v'),str(ROOT/'src/hdmi_lowlight_gain_rom.v')],
              [str(args.iverilog/'vvp.exe'),str(binary),'+vectors='+str(vectors),'+dump='+str(args.output/'rtl_badge_medium.hex')],
              [str(args.iverilog/'iverilog.exe'),'-g2012','-i','-s','lowlight_top_tb','-o',str(args.output/'lowlight_top.vvp'),
               str(ROOT/'sim/lowlight_top_tb.v'),str(ROOT/'src/hdmi_loop.v'),str(ROOT/'src/hdmi_lowlight_v2.v'),
               str(ROOT/'src/hdmi_lowlight_gain_rom.v'),str(ROOT/'src/pcie/drive_feedback_leds.v')],
              [str(args.iverilog/'vvp.exe'),str(args.output/'lowlight_top.vvp')]]
    results=[]
    for command in commands:
        result=subprocess.run(command,env=env,capture_output=True,text=True)
        results.append(dict(command=command,returncode=result.returncode,stdout=result.stdout,stderr=result.stderr))
        print(result.stdout,end='');print(result.stderr,end='',file=sys.stderr)
        if result.returncode:raise SystemExit(result.returncode)
    properties=verify_properties()
    pixels=np.array([int(value,16) for value in (args.output/'rtl_badge_medium.hex').read_text().splitlines()],dtype=np.uint32)
    assert len(pixels)==320*180
    image=np.stack(((pixels>>16)&255,(pixels>>8)&255,pixels&255),axis=1).astype(np.uint8).reshape(180,320,3)
    assert np.any(np.all(image[16:44,16:142]==(72,224,232),axis=2))
    cv2.imwrite(str(args.output/'rtl_badge_medium.png'),cv2.cvtColor(image,cv2.COLOR_RGB2BGR))
    report=dict(version='lowlight-v2',board_verified=False,source=str(source),checks=properties,rtl=results)
    (args.output/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('LOWLIGHT_VERIFICATION_PASS',json.dumps(properties))


if __name__=='__main__':main()
