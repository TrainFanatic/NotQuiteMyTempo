import sys
sys.path.insert(0,'/Users/adambanks/not-quite-my-tempo-assets/scripts')
from human_source import *
v,u,f,s,j,p=terence_source()
for name,b in s['bones'].items():
 if name in ['root','head','jaw','spine01','spine02','spine03','spine04','spine05','neck01','neck02','neck03','upperarm01.L','upperarm02.L','lowerarm01.L','lowerarm02.L','wrist.L','foot.L','eye.L','tongue00']:
  print(name,'head',j[b['head']].round(3),'tail',j[b['tail']].round(3),'parent',b['parent'])
print('BODY_BOUNDS',v[:13380].min(axis=0),v[:13380].max(axis=0))
