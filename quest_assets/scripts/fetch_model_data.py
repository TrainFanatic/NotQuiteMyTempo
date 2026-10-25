from pathlib import Path
import subprocess,json,zipfile,io,concurrent.futures
R=Path('/Users/adambanks/not-quite-my-tempo-assets')
def fetch(url):return subprocess.check_output(['curl','--fail','--location','--silent','--show-error','--max-time','90',url])
archive=R/'work/makehuman_master.zip'
if not archive.exists():archive.write_bytes(fetch('https://codeload.github.com/makehumancommunity/makehuman/zip/refs/heads/master'))
with zipfile.ZipFile(archive) as z:
 for info in z.infolist():
  rel=info.filename.split('/',1)[-1]
  if rel.startswith('makehuman/data/') and not info.is_dir():
   out=R/'work/source_assets'/rel;out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(z.read(info))
print('EXTRACTED_DATA',archive.stat().st_size,flush=True)
images=[('face_three_quarter.jpg','https://www.slashfilm.com/img/gallery/j-k-simmons-has-way-more-in-common-with-his-whiplash-character-than-you-think/l-intro-1763129479.jpg'),('face_front.jpg','https://m.media-amazon.com/images/M/MV5BNjc3ODIwZjItZTc1ZC00YTk3LWJiYTMtY2UwMmJiOWFiYzRkXkEyXkFqcGc%40._V1_.jpg'),('face_shout.jpg','https://www.slashfilm.com/img/gallery/heres-why-damien-chazelles-whiplash-was-produced-by-horror-studio-blumhouse/l-intro-1676246142.jpg'),('rehearsal_pose.jpg','https://industrialscripts.com/wp-content/uploads/2020/08/Whiplash-Scene-Not-Quite-My-Tempo.jpg')]
for origin in ['locations','costume']:
 urls=json.loads((R/f'refs/terence/{origin}_images.json').read_text())
 urls=[u for u in urls if (origin=='locations' and '/s1' in u and 'Whiplash' in u) or (origin=='costume' and 'images.propstore.com/' in u)]
 images += [(origin+'_'+str(i)+'.jpg',url) for i,url in enumerate(urls)]
def save(entry):
 name,url=entry
 try:(R/'refs/terence'/name).write_bytes(fetch(url));return name,'ok'
 except Exception as e:return name,str(e)
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
 for result in ex.map(save,images):print(result,flush=True)
(R/'refs/terence/source_manifest.json').write_text(json.dumps([{'file':n,'url':u,'usage':'modeling reference only; never a texture'} for n,u in images],indent=2))
