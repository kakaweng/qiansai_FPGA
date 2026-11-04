"""Integer model of the FPGA pipeline, for review and RTL parity (not a new model)."""
import sys
from pathlib import Path
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
from generate_lowlight_lut import make_tables

TABLES=make_tables()


def enhance(rgb,strength=1):
    rgb=np.asarray(rgb,dtype=np.uint8)
    current=rgb.astype(np.int32)
    filtered=current.copy()
    if rgb.shape[1]>=3:
        left,center,right=current[:,:-2],current[:,1:-1],current[:,2:]
        eligible=(center.max(axis=2)<112)&(np.abs(left-center).max(axis=2)<=12)&(np.abs(right-center).max(axis=2)<=12)
        average=(left+2*center+right+2)>>2
        filtered[:,1:-1]=np.where(eligible[:,:,None],average,center)
    maximum=filtered.max(axis=2)
    gains=np.array(TABLES[strength]['gain_q8'],dtype=np.int32)[maximum]
    result=(filtered*gains[:,:,None]+128)>>8
    assert result.max()<=255 and result.min()>=0
    return result.astype(np.uint8)


def legacy(rgb,threshold=110):
    data=np.asarray(rgb,dtype=np.uint8).astype(np.int32)
    y=(data[:,:,0]*77+data[:,:,1]*150+data[:,:,2]*29)>>8
    out=np.where((y<threshold//2)[:,:,None],data*4,
                 np.where((y<threshold)[:,:,None],data*2,data+(data>>2)))
    return np.clip(out,0,255).astype(np.uint8)
