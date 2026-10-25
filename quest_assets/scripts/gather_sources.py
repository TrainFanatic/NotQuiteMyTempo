from pathlib import Path
import urllib.request, json, re, hashlib, subprocess
ROOT=Path('/Users/adambanks/not-quite-my-tempo-assets')
UA={'User-Agent':'Mozilla/5.0 asset-reference-research'}
def get(url):
    return subprocess.check_output(['curl','--fail','--location','--silent','--show-error','--max-time','60',url])
def save(url,relative):
    p=ROOT/relative; p.parent.mkdir(parents=True,exist_ok=True)
    if not p.exists(): p.write_bytes(get(url))
    return p
sources=[('https://theatresinmovies.blogspot.com/2016/09/whiplash.html','locations'),('https://film-grab.com/2015/05/22/whiplash/','filmgrab'),('https://propstore.com/product/whiplash/fletchers-j-k-simmons-costume-with-hat/','costume'),('https://www.backstage.com/magazine/article/speech-cadence-tips-76732/','rehearsal')]
for url,name in sources:
    try:
        p=save(url,'refs/terence/'+name+'.html'); html=p.read_text(errors='replace')
        urls=list(dict.fromkeys(re.findall(r'https?[^\s\"<>]+?\.(?:jpg|jpeg|png)(?:\?[^\s\"<>]*)?',html)))
        (ROOT/('refs/terence/'+name+'_images.json')).write_text(json.dumps(urls,indent=2))
        print(name, len(urls), urls[:12],flush=True)
    except Exception as e: print(name,str(e),flush=True)
tree=json.loads(get('https://api.github.com/repos/makehumancommunity/makehuman/git/trees/master?recursive=1'))
(ROOT/'work/makehuman_tree.json').write_text(json.dumps(tree,indent=2))
paths=[v['path'] for v in tree.get('tree',[]) if v.get('type')=='blob']
print('ASSET PATHS',*[p for p in paths if any(s in p for s in ['/rigs/','/3dobjs/','/skins/'])],sep='\n')
print('MACRO TARGETS',*[p for p in paths if 'macrodetails' in p][:100],sep='\n')
for path in ['LICENSE.md','LICENSE.ASSETS.md','makehuman/data/3dobjs/base.obj','makehuman/data/rigs/default.mhskel','makehuman/data/rigs/default_weights.mhw']:
    try: save('https://raw.githubusercontent.com/makehumancommunity/makehuman/master/'+path,'work/source_assets/'+path); print('SAVED',path,flush=True)
    except Exception as e: print('ERROR',path,str(e))
