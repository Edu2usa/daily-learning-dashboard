#!/usr/bin/env python3
import argparse,json,os,re,shutil,tempfile
from pathlib import Path
DATE_RE=re.compile(r'^\d{4}-\d{2}-\d{2}$')
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--source',type=Path,required=True);ap.add_argument('--dest',type=Path,required=True);a=ap.parse_args()
 if not a.source.is_dir(): raise SystemExit('missing source directory')
 for name in ('bots.json','books-read.md'):
  if not (a.source/name).is_file(): raise SystemExit('missing '+name)
 parent=a.dest.parent;stage=Path(tempfile.mkdtemp(prefix='.public-data-',dir=parent))
 try:
  shutil.copy2(a.source/'bots.json',stage/'bots.json');shutil.copy2(a.source/'books-read.md',stage/'books-read.md')
  json.loads((stage/'bots.json').read_text(encoding='utf-8'))
  for day in sorted(a.source.iterdir()):
   if not day.is_dir() or not DATE_RE.match(day.name): continue
   out=stage/day.name;out.mkdir()
   for src in sorted(day.glob('*.md')):
    shutil.copy2(src,out/src.name)
  old=a.dest.with_name(a.dest.name+'.old')
  if old.exists(): shutil.rmtree(old)
  if a.dest.exists(): os.replace(a.dest,old)
  os.replace(stage,a.dest)
  if old.exists(): shutil.rmtree(old)
 finally:
  if stage.exists(): shutil.rmtree(stage)
 print(a.dest)
if __name__=='__main__': main()
