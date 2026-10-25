from pathlib import Path
p=Path('/Users/adambanks/not-quite-my-tempo-assets/scripts/human_source.py')
s=p.read_text().replace("'head/head-rectangular.target':.28", "'head/head-rectangular.target':.06").replace("'head/head-scale-horiz-incr.target':.12", "'head/head-scale-horiz-incr.target':.05").replace("'nose/nose-point-width-incr.target':.23", "'nose/nose-point-width-incr.target':.40")
s=s.replace("for name,f in params.items():", "for side in ['l','r']:\n  params[f'eyes/{side}-eye-height1-incr.target']=.16\n  params[f'eyes/{side}-eye-height2-incr.target']=.20\n  params[f'eyes/{side}-eye-height3-incr.target']=.12\n for name,f in params.items():")
p.write_text(s)
p=Path('/Users/adambanks/not-quite-my-tempo-assets/scripts/build_terence_blockout.py')
s=p.read_text().replace('import bpy, numpy as np','import bpy, bmesh, numpy as np')
s=s.replace("mesh_source[name]=ids", "mesh_source[ob.name]=ids")
s=s.replace("body=mesh('terence_skin',groups['body'])", "body=mesh('terence_skin',groups['body'],filter_fn=lambda p:p[2]>.10)")
s=s.replace("(.012,.012,.012)","(.013,.013,.013)")
insert='''# Smooth garment boundary loops while preserving all skin vertex groups.
for ob in [shirt,pants]:
 bm=bmesh.new();bm.from_mesh(ob.data)
 boundary={v for e in bm.edges if e.is_boundary for v in e.verts}
 for step in range(12):
  updates={}
  for vtx in boundary:
   ns=[e.other_vert(vtx) for e in vtx.link_edges if e.is_boundary]
   if len(ns)==2:updates[vtx]=vtx.co.lerp((ns[0].co+ns[1].co)*.5,.45)
  for vtx,co in updates.items():vtx.co=co
 bm.to_mesh(ob.data);bm.free()
 bpy.context.view_layer.objects.active=ob
 sub=ob.modifiers.new('garment_surface','SUBSURF');sub.levels=1;bpy.ops.object.modifier_apply(modifier=sub.name)
 solid=ob.modifiers.new('garment_thickness','SOLIDIFY');solid.thickness=.0015;bpy.ops.object.modifier_apply(modifier=solid.name)
'''
s=s.replace('# Loafer blockout',insert+'\n# Loafer blockout')
s=s.replace("'terence_v001.blend'", "'terence_v002.blend'").replace("f'v001_{name}.png'", "f'v002_{name}.png'")
s=s.replace("'front':((0,-5,1.0),(0,0,1.0),'ORTHO',2.35)","'front':((.4,-5,1.0),(.4,0,1.0),'ORTHO',3.15)")
Path('/Users/adambanks/not-quite-my-tempo-assets/scripts/build_terence_v002.py').write_text(s)
