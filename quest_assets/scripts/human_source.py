from pathlib import Path
from collections import defaultdict
import json, numpy as np
ROOT=Path('/Users/adambanks/not-quite-my-tempo-assets')
DATA=ROOT/'work/source_assets/makehuman/data'
def read_source():
 verts=[];uv=[];faces=defaultdict(list);group='body'
 for line in (DATA/'3dobjs/base.obj').read_text().splitlines():
  p=line.split()
  if not p:continue
  if p[0]=='v':verts.append([float(x) for x in p[1:4]])
  elif p[0]=='vt':uv.append([float(x) for x in p[1:3]])
  elif p[0]=='g':group=p[1]
  elif p[0]=='f':
   idx=[];ti=[]
   for corner in p[1:]:
    a=corner.split('/');idx.append(int(a[0])-1);ti.append(int(a[1])-1 if len(a)>1 and a[1] else 0)
   faces[group].append((idx,ti))
 return np.array(verts),uv,faces

def target_delta(name,n):
 out=np.zeros((n,3)); p=DATA/'targets'/name
 for line in p.read_text().splitlines():
  t=line.split()
  if len(t)==4 and not line.startswith('#'):out[int(t[0])]=list(map(float,t[1:]))
 return out

def terence_source():
 v,uv,faces=read_source()
 params={'macrodetails/caucasian-male-old.target':.62,'macrodetails/caucasian-male-young.target':.38,
 'macrodetails/universal-male-old-averagemuscle-averageweight.target':.4,
 'macrodetails/universal-male-old-maxmuscle-averageweight.target':.22,
 'macrodetails/universal-male-young-averagemuscle-averageweight.target':.24,
 'macrodetails/universal-male-young-maxmuscle-averageweight.target':.14,
 'head/head-scale-horiz-incr.target':.05,'head/head-oval.target':.65,
 'head/head-fat-decr.target':.12,'chin/chin-width-decr.target':.20,'chin/chin-prominent-incr.target':.14,
 'nose/nose-scale-vert-incr.target':.13,'nose/nose-point-width-incr.target':.60,'nose/nose-scale-depth-incr.target':.12,
 'mouth/mouth-scale-horiz-incr.target':.12,'mouth/mouth-upperlip-height-decr.target':.33,
 'mouth/mouth-lowerlip-height-decr.target':.18,'mouth/mouth-laugh-lines-in.target':.55,
 'forehead/forehead-temple-decr.target':.30}
 for side in ['l','r']:
  params.update({f'eyes/{side}-eye-bag-incr.target':.32,f'eyes/{side}-eye-eyefold-down.target':.18,
  f'cheek/{side}-cheek-inner-decr.target':.3,f'cheek/{side}-cheek-bones-incr.target':.18,
  f'ears/{side}-ear-scale-incr.target':.12})
 for side in ['l','r']:
  params[f'eyes/{side}-eye-height1-incr.target']=.45
  params[f'eyes/{side}-eye-height2-incr.target']=.65
  params[f'eyes/{side}-eye-height3-incr.target']=.35
 for name,f in params.items():v+=target_delta(name,len(v))*f
 body_idx=sorted({i for f,_ in faces['body'] for i in f})
 lo=v[body_idx,1].min();hi=v[body_idx,1].max();scale=1.80/(hi-lo)
 v=np.column_stack((v[:,0]*scale,-v[:,2]*scale,(v[:,1]-lo)*scale))
 sk=json.loads((DATA/'rigs/default.mhskel').read_text())
 joints={name:v[ids].mean(axis=0) for name,ids in sk['joints'].items()}
 return v,uv,faces,sk,joints,params
if __name__=='__main__':
 v,u,f,s,j,p=terence_source()
 for name,b in s['bones'].items():
  if name in ['root','head','jaw','spine01','spine02','spine03','spine04','spine05','neck01','neck02','neck03','upperarm01.L','upperarm02.L','lowerarm01.L','lowerarm02.L','wrist.L','foot.L','eye.L','tongue00']:
   print(name,'head',j[b['head']].round(3),'tail',j[b['tail']].round(3),'parent',b['parent'])
