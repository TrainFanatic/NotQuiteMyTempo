import sys, json, math
from pathlib import Path
import bpy, numpy as np
from mathutils import Vector, Matrix
sys.path.insert(0,'/Users/adambanks/not-quite-my-tempo-assets/scripts')
from human_source import *
R=ROOT
OUT=R/'work/terence';OUT.mkdir(exist_ok=True)
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
sc=bpy.context.scene;sc.unit_settings.system='METRIC';sc.unit_settings.scale_length=1
sc.render.engine='BLENDER_EEVEE';sc.render.resolution_x=640;sc.render.resolution_y=640;sc.render.resolution_percentage=100
sc.render.image_settings.file_format='PNG';sc.world.color=(.18,.18,.18)
sc.view_settings.view_transform='AgX'
assets=bpy.data.collections.new('terence_asset');sc.collection.children.link(assets)
preview=bpy.data.collections.new('preview_only');sc.collection.children.link(preview)
def move(obj,col):
 for c in list(obj.users_collection):c.objects.unlink(obj)
 col.objects.link(obj)
def mat(name,col):
 m=bpy.data.materials.new(name);m.use_nodes=True;p=m.node_tree.nodes.get('Principled BSDF');p.inputs['Base Color'].default_value=(*col,1);p.inputs['Roughness'].default_value=.68;return m
gray=mat('blockout_clay',(.38,.4,.42));dark=mat('blockout_dark_clay',(.23,.25,.27));floor=mat('preview_floor',(.11,.13,.15))
v,uv,groups,sk,joints,params=terence_source()
source_weights=json.loads((DATA/'rigs/default_weights.mhw').read_text())['weights']
weights=[[] for _ in v]
for name,entries in source_weights.items():
 for i,w in entries:weights[i].append((name,w))
for i,ws in enumerate(weights):
 ws=sorted(ws,key=lambda q:-q[1])[:4];s=sum(w for _,w in ws)
 weights[i]=[(n,w/s) for n,w in ws] if s else [('head' if v[i,2]>1.55 else 'root',1)]
arm=bpy.data.armatures.new('terence_skeleton');rig=bpy.data.objects.new('terence_rig',arm);assets.objects.link(rig)
bpy.context.view_layer.objects.active=rig;rig.select_set(True);bpy.ops.object.mode_set(mode='EDIT')
for name,d in sk['bones'].items():
 b=arm.edit_bones.new(name);b.head=joints[d['head']];b.tail=joints[d['tail']]
 if b.length<.0001:b.tail.z+=.001
for name,d in sk['bones'].items():
 if d['parent']:arm.edit_bones[name].parent=arm.edit_bones[d['parent']]
bpy.ops.object.mode_set(mode='OBJECT');rig.select_set(False)
mesh_source={}
def mesh(name,faces,material=gray,offset=0,filter_fn=None):
 faces=[f for f in faces if filter_fn is None or filter_fn(np.mean(v[f[0]],axis=0))]
 ids=sorted({i for f,_ in faces for i in f});mp={old:new for new,old in enumerate(ids)}
 me=bpy.data.meshes.new(name);me.from_pydata(v[ids].tolist(),[],[[mp[i] for i in f] for f,_ in faces]);me.update()
 ob=bpy.data.objects.new(name,me);assets.objects.link(ob);ob.data.materials.append(material)
 layer=me.uv_layers.new(name='uv')
 for poly,(_,tis) in zip(me.polygons,faces):
  poly.use_smooth=True
  for li,ti in zip(poly.loop_indices,tis):layer.data[li].uv=uv[ti]
 if offset:
  for vertex in me.vertices:vertex.co+=vertex.normal*offset
 for name in source_weights:ob.vertex_groups.new(name=name)
 for new,old in enumerate(ids):
  for n,w in weights[old]:ob.vertex_groups[n].add([new],w,'REPLACE')
 mod=ob.modifiers.new('skin','ARMATURE');mod.object=rig;ob.parent=rig
 mesh_source[name]=ids
 return ob
body=mesh('terence_skin',groups['body'])
shirt=mesh('terence_tshirt',groups['helper-tights'],dark,.006,lambda p:p[2]>1.035 and p[2]<1.53 and abs(p[0])<.30)
pants=mesh('terence_trousers',groups['helper-tights'],dark,.008,lambda p:.10<p[2]<1.055)
# Enlarge lower trouser legs slightly for woven fabric rather than skin-tight leggings.
for p in pants.data.vertices:
 if p.co.z<.85:
  s=1 if p.co.x>0 else -1;center=s*(.14+.05*(1-p.co.z/.85));p.co.x=center+(p.co.x-center)*1.10;p.co.y*=1.12
for obj,group in [('terence_teeth_upper','helper-upper-teeth'),('terence_teeth_lower','helper-lower-teeth'),('terence_tongue','helper-tongue')]:mesh(obj,groups[group])
# Eyes use actual spheres centered on the source eye joints, not the low-resolution helper cages.
def sphere(name,loc,scale,material,weight=None,col=assets):
 bpy.ops.mesh.primitive_uv_sphere_add(segments=24,ring_count=16,location=loc);ob=bpy.context.object;ob.name=name;ob.scale=scale;bpy.ops.object.transform_apply(location=False,rotation=False,scale=True);move(ob,col);ob.data.materials.append(material)
 for p in ob.data.polygons:p.use_smooth=True
 if weight:
  g=ob.vertex_groups.new(name=weight);g.add(list(range(len(ob.data.vertices))),1,'REPLACE');mod=ob.modifiers.new('skin','ARMATURE');mod.object=rig;ob.parent=rig
 return ob
for side in ['L','R']:
 center=joints[sk['bones']['eye.'+side]['head']]
 sphere('terence_eye_'+side.lower(),center,(.012,.012,.012),gray,'eye.'+side)
# Move each arm chain to a genuine horizontal T-pose, then bake that pose as rest.
bpy.context.view_layer.update()
for side,sgn in [('L',1),('R',-1)]:
 for name in ['upperarm01.','upperarm02.','lowerarm01.','lowerarm02.','wrist.']:
  pb=rig.pose.bones[name+side];m=pb.matrix.copy();direction=(pb.tail-pb.head).normalized();q=direction.rotation_difference(Vector((sgn,0,0)));m=q.to_matrix().to_4x4() @ m;m.translation=pb.head;pb.matrix=m;bpy.context.view_layer.update()
for ob in list(assets.objects):
 if ob.type=='MESH':
  bpy.context.view_layer.objects.active=ob
  for modifier in list(ob.modifiers):
   if modifier.type=='ARMATURE':bpy.ops.object.modifier_apply(modifier=modifier.name)
bpy.context.view_layer.objects.active=rig;rig.select_set(True);bpy.ops.object.mode_set(mode='POSE');bpy.ops.pose.armature_apply(selected=False);bpy.ops.object.mode_set(mode='OBJECT');rig.select_set(False)
for ob in list(assets.objects):
 if ob.type=='MESH':mod=ob.modifiers.new('skin','ARMATURE');mod.object=rig
# Loafer blockout, with flatter sole and rounded toe; no individual toe shapes.
def loafer(side,sgn):
 cx=sgn*.188;verts=[];faces=[];n=32
 for z,rx,ry,cy in [(.006,.048,.135,-.072),(.022,.053,.139,-.072),(.045,.052,.135,-.069),(.075,.043,.118,-.052),(.102,.036,.07,-.012)]:
  for i in range(n):
   a=2*math.pi*i/n;verts.append((cx+rx*math.cos(a),cy+ry*math.sin(a),z))
 for k in range(4):
  for i in range(n):faces.append((k*n+i,k*n+(i+1)%n,(k+1)*n+(i+1)%n,(k+1)*n+i))
 faces.append(tuple(reversed(range(n))));faces.append(tuple(range(4*n,5*n)))
 me=bpy.data.meshes.new('loafer');me.from_pydata(verts,[],faces);ob=bpy.data.objects.new('terence_loafer_'+side.lower(),me);assets.objects.link(ob);me.materials.append(dark)
 for p in me.polygons:p.use_smooth=True
 g=ob.vertex_groups.new(name='foot.'+side);g.add(list(range(len(verts))),1,'REPLACE');mod=ob.modifiers.new('skin','ARMATURE');mod.object=rig;ob.parent=rig
loafer('L',1);loafer('R',-1)
# Preview stage and a separate 1.8 m human-scale silhouette.
bpy.ops.mesh.primitive_plane_add(size=200);ground=bpy.context.object;ground.name='preview_ground';move(ground,preview);ground.data.materials.append(floor)
refmat=mat('scale_reference_clay',(.18,.24,.27))
for name,loc,scale in [('head',(1.25,.35,1.68),(.075,.07,.12)),('torso',(1.25,.35,1.30),(.18,.09,.26)),('pelvis',(1.25,.35,1.01),(.14,.09,.14)),('leg_l',(1.33,.35,.52),(.055,.06,.48)),('leg_r',(1.17,.35,.52),(.055,.06,.48)),('arm_l',(1.48,.35,1.18),(.045,.05,.30)),('arm_r',(1.02,.35,1.18),(.045,.05,.30))]:sphere('human_scale_1_8m_'+name,loc,scale,refmat,None,preview)
def area(name,pos,power,size):
 data=bpy.data.lights.new(name,'AREA');ob=bpy.data.objects.new(name,data);preview.objects.link(ob);ob.location=pos;data.energy=power;data.shape='DISK';data.size=size;ob.rotation_euler=(Vector((0,0,1))-ob.location).to_track_quat('-Z','Y').to_euler()
area('preview_key',(-3,-4,5),600,4);area('preview_fill',(4,-2,3),350,3);area('preview_rim',(1,3,4),700,3)
cam_data=bpy.data.cameras.new('preview_camera');cam=bpy.data.objects.new('preview_camera',cam_data);preview.objects.link(cam);sc.camera=cam
views={'front':((0,-5,1.0),(0,0,1.0),'ORTHO',2.35),'side':((5,0,1.0),(0,0,1.0),'ORTHO',2.35),'three_quarter':((3,-5,2.15),(0,0,.95),'ORTHO',2.35),'seated_eye':((0,-2.4,1.20),(0,0,1.12),'PERSP',2.35),'face':((0,-2,1.68),(0,-.03,1.68),'ORTHO',.40)}
(R/'work/terence/source_morphs.json').write_text(json.dumps(params,indent=2))
for n,ids in mesh_source.items():
 ob=bpy.data.objects.get(n)
 if ob:ob['source_vertex_ids']=ids
bpy.ops.wm.save_as_mainfile(filepath=str(OUT/'terence_v001.blend'))
for name,(pos,look,kind,ortho) in views.items():
 for ob in preview.objects:
  if ob.name.startswith('human_scale'):ob.hide_render=name!='front'
 cam.location=pos;cam.rotation_euler=(Vector(look)-cam.location).to_track_quat('-Z','Y').to_euler();cam_data.type=kind;cam_data.ortho_scale=ortho;cam_data.lens=40
 sc.render.filepath=str(OUT/f'v001_{name}.png');bpy.ops.render.render(write_still=True)
print('BLOCKOUT_COMPLETE',len(body.data.vertices),flush=True)
