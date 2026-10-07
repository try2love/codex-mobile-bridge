#!/usr/bin/env python3
"""Copy product UI into the offline website demo; never copy native credentials/data."""
import argparse, json, re, shutil, subprocess
from pathlib import Path
parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,default=Path('.'));args=parser.parse_args()
source=args.source.resolve();site=Path(__file__).resolve().parents[1]/'site'
target=site/'demo-product';target.mkdir(exist_ok=True)
for folder in ['web','desktop']:
 out=target/folder
 if out.exists():shutil.rmtree(out)
 if folder=='web':shutil.copytree(source/folder,out)
 else:
  out.mkdir()
  for p in (source/folder).iterdir():
   if p.suffix in ['.html','.css','.js']:shutil.copy2(p,out/p.name)
  (out/'assets').mkdir();shutil.copy2(source/'desktop/assets/icon.png',out/'assets/icon.png')
 p=out/'index.html';s=p.read_text()
 s=re.sub(r'<meta http-equiv="Content-Security-Policy"[^>]*>','',s)
 csp="default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'none'; form-action 'none'; base-uri 'none'; object-src 'none'"
 s=s.replace('<head>','<head><meta http-equiv="Content-Security-Policy" content="'+csp+'"><script src="../../demo-data/isolation.js"></script><script src="../../demo-data/accounts.js"></script><script src="../../demo-data/'+folder+'.js"></script>')
 if folder=='web':
  s=re.sub(r'(src|href)="/(?!/)',r'\1="./',s)
  s=s.replace('<link rel="manifest" href="./manifest.webmanifest">','')
  s=s.replace('</body>','<script src="../../demo-data/scenes.js"></script></body>')
 p.write_text(s)
shutil.copy2(source/'mobile/android/assets/mobile-ui.js',site/'demo-data/mobile-ui.js')
print('Demo UI copied from',subprocess.check_output(['git','rev-parse','--short','HEAD'],cwd=source,text=True).strip())
