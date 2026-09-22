import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, mkdirSync, writeFileSync, symlinkSync, lstatSync } from 'node:fs';
import {copyTree} from './copy-tree.mjs';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { EventEmitter } from 'node:events';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import {decodeTimeout, verifyMedia} from './verify-media.mjs';
import { build } from 'esbuild';
const require = createRequire(import.meta.url);
const tempRoot = process.env.PRODUCT_VIDEO_TEST_TMP ?? (process.platform === 'win32' ? 'D:/CodexTemp/2026-09-22/product-video-optimization' : tmpdir());
mkdirSync(tempRoot, {recursive:true});
const temp = mkdtempSync(path.join(tempRoot, 'workbench-check-'));
const load = name => import(pathToFileURL(path.join(temp, `${name}.mjs`)).href);
// Use isolated child-process doubles: revision/save tests never invoke Python,
// a speech service or an external API. Temporary evidence stays in tempRoot.
const testModules = {name:'product-video-test-modules',setup(context) {
  context.onResolve({filter:/^(react(?:\/.*)?|remotion)$/}, args => ({path:pathToFileURL(require.resolve(args.path)).href, external:true}));
  context.onResolve({filter:/^node:child_process$/}, () => ({path:'child-process-test',namespace:'test-child'}));
  context.onLoad({filter:/.*/,namespace:'test-child'}, () => ({contents:`
    import {EventEmitter} from 'node:events';
    export function spawn(command,args,options) {
      const child = new EventEmitter();
      child.stdout = new EventEmitter(); child.stderr = new EventEmitter();
      child.kill = () => true; child.exitCode = null;
      (globalThis.__productVideoChildren ??= []).push({child,command,args,options});
      return child;
    }`,loader:'js'}));
}};
try {
  await build({entryPoints:{Transition:'src/product-video/Transition.tsx',adaptation:'src/product-video/adaptation.tsx',Highlights:'src/cards/Highlights.tsx',server:'src/product-video/server.ts'},bundle:true,platform:'node',format:'esm',packages:'external',outdir:temp,outExtension:{'.js':'.mjs'},jsx:'automatic',plugins:[testModules]});
  const catalog=JSON.parse(readFileSync('../motion-catalog.json','utf8'));
  const frameCount=name=>catalog.motions.find(m=>m.component===name).frames;
  assert.equal(frameCount('LetterspaceMaterialize'),110);
  assert.equal(frameCount('TitleDemoteToLabel'),196);
  assert.equal(frameCount('PillSlotCycle'),175);
  const { TRANSITIONS, transitionStyles } = await load('Transition');
  const { parseMap, replaceText } = await load('adaptation');
  const { fittedMediaBox, highlightBox, highlightMotion, highlightLabel, placeHighlightLabel } = await load('Highlights');
  const landscape = fittedMediaBox(1920,1080,1920,1080);
  assert.deepEqual(landscape,{x:0,y:0,width:1920,height:1080});
  const portrait = fittedMediaBox(1080,1920,1920,1080);
  assert.deepEqual(portrait,{x:656.25,y:0,width:607.5,height:1080});
  assert.deepEqual(highlightBox([0,0,.5,.5],portrait,1920,1080),{x:656.25,y:0,width:303.75,height:540});
  const square = fittedMediaBox(1000,1000,1920,1080);
  assert.deepEqual(highlightBox([.1,.2,.3,.4],square,1920,1080),{x:528,y:216,width:324,height:432.0000000000001});
  const cover = fittedMediaBox(1080,1920,1920,1080,'cover');
  assert.equal(highlightBox([0,0,.1,.1],cover,1920,1080),null);
  const cropped = highlightBox([.25,.25,.5,.5],cover,1920,1080);
  assert.equal(cropped.x,480); assert.equal(cropped.width,960); assert.equal(cropped.y,0); assert.equal(cropped.height,1080);
  assert.equal(fittedMediaBox(0,1080,1920,1080),null);
  assert.equal(highlightBox([.8,.2,.3,.1],landscape,1920,1080),null);
  assert.equal(highlightBox([0,0,NaN,.1],landscape,1920,1080),null);
  for (const fps of [24,30,60]) {
    assert.equal(highlightMotion(0,fps,.3,3).opacity,0);
    assert.equal(highlightMotion(fps,fps,.3,3).opacity,1);
    assert.equal(highlightMotion(3*fps,fps,.3,3).opacity,0);
    assert.deepEqual(highlightMotion(fps*.3,fps,.3,3,true),{opacity:1,offset:0});
    assert.deepEqual(highlightMotion(fps*2.999,fps,.3,3,true),{opacity:1,offset:0});
  }
  assert(highlightMotion(12,30,.3,3).opacity > 0 && highlightMotion(12,30,.3,3).opacity < 1);
  assert(highlightMotion(88,30,.3,3).opacity > 0 && highlightMotion(88,30,.3,3).opacity < 1);
  assert.equal(highlightMotion(0,0,0,1).opacity,0);
  assert.equal(highlightLabel('  点击\n保存  '),'点击 保存');
  assert.equal(Array.from(highlightLabel('一'.repeat(30))).length,24);
  const control={x:1700,y:920,width:180,height:90};
  const label=placeHighlightLabel(control,'保存结果',1920,1080);
  assert(label); assert(label.x>=24 && label.x+label.width<=1896); assert(label.y+label.height<=864);
  assert(label.x+label.width<control.x || label.x>control.x+control.width || label.y+label.height<control.y);
  assert.equal(placeHighlightLabel({x:0,y:0,width:1920,height:1080},'不能遮住内容',1920,1080),null);
  assert.equal(placeHighlightLabel(control,'保存结果',1920,1080,[{x:0,y:0,width:1920,height:864}]),null);
  const { productVideoPlugin } = await load('server');
  const sourceFile=path.join(temp,'source-project.json'), studio=path.join(temp,'studio');
  const output=path.join(temp,'output');
  mkdirSync(studio); mkdirSync(output);
  const original={product:{name:'Revision test'},voice:{mode:'none'},chapters:[{id:'one',title:'第一章',narration:'原文',duration:4}],output:'output'};
  const originalText=JSON.stringify(original,null,2);
  writeFileSync(sourceFile,originalText); writeFileSync(path.join(studio,'project.json'),'{}');
  const envKeys=['PRODUCT_VIDEO_PROJECT','PRODUCT_VIDEO_STUDIO','PRODUCT_VIDEO_PYTHON'];
  const oldEnv=Object.fromEntries(envKeys.map(key=>[key,process.env[key]]));
  Object.assign(process.env,{PRODUCT_VIDEO_PROJECT:sourceFile,PRODUCT_VIDEO_STUDIO:studio,PRODUCT_VIDEO_PYTHON:'isolated-python-double'});
  const handlers=[];
  const register=()=>{const plugin=productVideoPlugin(); plugin.configureServer({middlewares:{use:(_route,handler)=>handlers.push(handler)},httpServer:new EventEmitter()});return handlers.at(-1);};
  const request=(handler,method,url,body)=>new Promise(resolve=>{
    const req=new EventEmitter(); Object.assign(req,{method,url,destroy(){}});
    const res={statusCode:0,setHeader(){},end(text){resolve({status:res.statusCode,body:JSON.parse(text)});}};
    handler(req,res);
    if(method==='POST'){req.emit('data',JSON.stringify(body));req.emit('end');}
  });
  try {
    const handler=register();
    const first=await request(handler,'GET','/');
    assert.equal(first.status,200);
    assert.equal((await request(handler,'GET','/')).body.revision,first.body.revision);
    const chapters=[{id:'one',narration:'更新后文稿',duration:5}];
    writeFileSync(sourceFile,`${originalText}\n`);
    assert.equal((await request(handler,'POST','/voice',{revision:first.body.revision,chapters})).status,409);
    assert.equal(readFileSync(sourceFile,'utf8'),`${originalText}\n`);
    const refreshed=await request(handler,'GET','/');
    assert.notEqual(refreshed.body.revision,first.body.revision);
    const restarted=register();
    assert.equal((await request(restarted,'POST','/voice',{revision:refreshed.body.revision,chapters})).status,409);
    const fresh=(await request(restarted,'GET','/')).body.revision;
    assert.equal((await request(restarted,'POST','/voice',{revision:fresh,chapters})).status,202);
    assert.equal(JSON.parse(readFileSync(sourceFile,'utf8')).chapters[0].narration,'更新后文稿');
    assert.equal((await request(restarted,'POST','/voice',{revision:fresh,chapters})).status,409);
    const spawned=globalThis.__productVideoChildren.at(-1);
    assert.equal(spawned.command,'isolated-python-double'); assert(!spawned.args.includes('--generate-voice')); assert.equal(spawned.options.windowsHide,true);
    writeFileSync(path.join(output,'studio.json'),JSON.stringify({directory:studio,project:path.join(studio,'project.json')}));
    spawned.child.exitCode=0; spawned.child.emit('close',0);
    const completed=(await request(restarted,'GET','/job')).body;
    assert.equal(completed.status,'done'); assert.notEqual(completed.revision,fresh);
    assert.equal((await request(restarted,'GET','/')).body.revision,completed.revision);
    assert.equal((await request(restarted,'POST','/voice',{revision:fresh,chapters})).status,409);
  } finally {
    for (const [key,value] of Object.entries(oldEnv)) {if(value===undefined) delete process.env[key]; else process.env[key]=value;}
    for (const spawned of globalThis.__productVideoChildren ?? []) {if(spawned.child.exitCode===null) {spawned.child.exitCode=1;spawned.child.emit('close',1);}}
    delete globalThis.__productVideoChildren;
  }
  for (const kind of TRANSITIONS) {
    assert.deepEqual(transitionStyles(kind,1,1920,1080),[{opacity:0},{}]);
    if (kind !== 'cut') assert.deepEqual(transitionStyles(kind,0,1920,1080),[{}, {opacity:0}]);
    const middle=transitionStyles(kind,.5,1920,1080);
    assert(!JSON.stringify(middle).includes('NaN'));
  }
  const [left]=transitionStyles('slide-left',.5,1920,1080);
  const [right]=transitionStyles('slide-right',.5,1920,1080);
  assert.equal(left.transform,'translateX(-960px)'); assert.equal(right.transform,'translateX(960px)');
  assert.notDeepEqual(transitionStyles('iris',.5,1920,1080),transitionStyles('fade',.5,1920,1080));
  assert.equal(replaceText('  Original  ',{Original:'产品视频'}),'  产品视频  ');
  assert.equal(replaceText('Original version',{Original:'产品视频'}),'Original version');
  assert.deepEqual(parseMap('{"Title":"产品视频"}','copy'),{Title:'产品视频'});
  assert.throws(()=>parseMap('{"Title":7}','copy'));
  assert.throws(()=>parseMap('[]','copy'));
  const timeout=decodeTimeout(330.9);
  assert(Number.isInteger(timeout));
  assert.equal(spawnSync(process.execPath,['-e','process.exit(0)'],{timeout}).status,0);
  {
    const source=path.join(temp,'source'), linked=path.join(temp,'linked'), copied=path.join(temp,'copied');
    mkdirSync(source); mkdirSync(linked);
    writeFileSync(path.join(source,'中文 file.txt'),'字幕与素材','utf8');
    symlinkSync(source,path.join(linked,'assets'),process.platform==='win32'?'junction':'dir');
    copyTree(linked,copied);
    assert.equal(readFileSync(path.join(copied,'assets','中文 file.txt'),'utf8'),'字幕与素材');
    assert.equal(lstatSync(path.join(copied,'assets')).isSymbolicLink(),false);
    symlinkSync(linked,path.join(source,'cycle'),process.platform==='win32'?'junction':'dir');
    assert.throws(()=>copyTree(linked,path.join(temp,'cycle-copy')),/Circular link/);
    const movie=path.join(temp,'long.mp4');
    const encoded=spawnSync(process.env.PRODUCT_VIDEO_FFMPEG ?? 'ffmpeg',['-v','error','-f','lavfi','-i','color=size=160x90:rate=30','-f','lavfi','-i','anullsrc=r=8000:cl=mono','-t','330.9','-c:v','libx264','-preset','ultrafast','-c:a','aac',movie],{encoding:'utf8',timeout:120000,windowsHide:true});
    assert.equal(encoded.status,0,encoded.stderr);
    const composition={width:160,height:90,fps:30,durationInFrames:9927};
    assert.equal(verifyMedia(movie,composition).full_decode,'passed');
    assert.throws(()=>verifyMedia(movie,{...composition,width:320}),/未通过校验/);
  }
  console.log('Media-fit highlights, local frame timing, caption-safe labels and concurrent save behavior passed.');
  console.log('15 transition endpoints, direction, content scope and malformed mapping checks passed.');
  console.log('330.9-second export verification passed with an integer process timeout.');
} finally {
  console.log(`Test evidence retained in ${temp}`);
}
