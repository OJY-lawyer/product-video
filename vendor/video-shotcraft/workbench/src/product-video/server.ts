import { randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';
import { readFileSync, renameSync, writeFileSync } from 'node:fs';
import { copyTree } from '../../scripts/copy-tree.mjs';
import path from 'node:path';
import type { Plugin } from 'vite';

type Job = { status: 'running' | 'done' | 'error'; message: string; project?: unknown; revision?: string };
export function productVideoPlugin(): Plugin {
  // Tokens are scoped to this server instance. Compare source text directly so
  // external edits (including formatting changes) invalidate an earlier save.
  const session = randomUUID();
  let sourceRevision: { text: string; token: string } | null = null;
  let revisionSerial = 0;
  const revisionOf = (text: string) => {
    if (!sourceRevision || sourceRevision.text !== text) sourceRevision = { text, token: `${session}:${++revisionSerial}` };
    return sourceRevision.token;
  };
  let job: Job | null = null;
  let serial = 0;
  return { name: 'product-video-voice', configureServer(server) {
    const source = process.env.PRODUCT_VIDEO_PROJECT;
    const studio = process.env.PRODUCT_VIDEO_STUDIO;
    const python = process.env.PRODUCT_VIDEO_PYTHON;
    server.middlewares.use('/api/product-video', (req, res) => {
      const send = (status: number, body: unknown) => { res.statusCode = status; res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(body)); };
      if (!source || !studio || !python) { send(409, { error: '请通过 product-video studio 打开一个配音项目。' }); return; }
      const endpoint = (req.url ?? '/').split('?')[0];
      if (req.method === 'GET' && endpoint === '/job') { send(200, job); return; }
      if (req.method === 'GET' && endpoint === '/') {
        try {
          const sourceText = readFileSync(source, 'utf8');
          const config = JSON.parse(sourceText);
          const voices = config.voice?.mode === 'none' ? [] : JSON.parse(readFileSync(path.resolve('../../../scripts/engine/product_video/data/voices.json'), 'utf8')).voices.map((v: { id: string; names: string[] }) => ({ id: v.id, names: v.names }));
          send(200, { revision: revisionOf(sourceText), name: config.product.name, voice: config.voice ?? {}, voices, chapters: config.chapters.map((c: { id: string; title: string; narration: string; duration?: number }) => ({ id: c.id, title: c.title, narration: c.narration, duration: c.duration })), project: JSON.parse(readFileSync(path.join(studio, 'project.json'), 'utf8')) });
        } catch { send(500, { error: '无法读取当前配音项目。' }); }
        return;
      }
      if (req.method !== 'POST' || endpoint !== '/voice') { send(404, { error: '接口不存在。' }); return; }
      if (job?.status === 'running') { send(409, { error: '配音任务正在运行。' }); return; }
      let raw = '', oversized = false;
      req.on('data', chunk => { raw += chunk; if (raw.length > 1_000_000) { oversized = true; req.destroy(); } });
      req.on('end', () => {
        if (oversized) return;
        if (job?.status === 'running') { send(409, { error: '配音任务正在运行。' }); return; }
        try {
          const update = JSON.parse(raw);
          const sourceText = readFileSync(source, 'utf8');
          const config = JSON.parse(sourceText);
          const originalConfig = JSON.stringify(config);
          const silent = config.voice?.mode === 'none';
          if (update.revision !== revisionOf(sourceText)) { send(409, { error: '项目已在其他位置修改，请刷新工作台后再生成。' }); return; }
          if ((!silent && (typeof update.speaker !== 'string' || !update.speaker.trim())) || !Array.isArray(update.chapters) || update.chapters.length !== config.chapters.length) throw new Error('请填写每章文稿；配音项目还需选择音色。');
          config.chapters = config.chapters.map((chapter: { id: string; narration: string; duration?: number; captions?: {end:number}[] }, i: number) => {
            const next = update.chapters[i];
            if (next.id !== chapter.id || typeof next.narration !== 'string' || !next.narration.trim() || next.narration.length > 2000) throw new Error('章节不匹配，或旁白超过 2000 字。');
            if (next.narration !== chapter.narration) delete chapter.captions;
            if (silent) {
              if (typeof next.duration !== 'number' || !Number.isFinite(next.duration) || next.duration < .25 || next.duration > 3600) throw new Error('每章时长须为 0.25–3600 秒。');
              if (chapter.captions?.some(cue => cue.end > next.duration)) throw new Error('自定义字幕超过新的章节时长，请先调整字幕。');
            }
            return { ...chapter, narration: next.narration, ...(silent ? {duration:next.duration} : {}) };
          });
          if (!silent) {
            const changedVoice = config.voice?.speaker !== update.speaker.trim();
            config.voice = { ...config.voice, speaker: update.speaker.trim() };
            // Resolve model/transport with the existing voice selector before generation.
            if (changedVoice) { delete config.voice.resource_id; delete config.voice.transport; }
          }
          config.schema_version = 2;
          config.video = { ...config.video, renderer: 'remotion' };
          const pending = `${source}.pending-${process.pid}-${++serial}`;
          if (JSON.stringify(config) !== originalConfig) { writeFileSync(pending, JSON.stringify(config, null, 2)); renameSync(pending, source); }
          job = { status: 'running', message: silent ? '正在按章节时长更新字幕与镜头…' : '正在生成缺失的配音并对齐镜头…', revision: revisionOf(readFileSync(source, 'utf8')) };
          const current = job;
          const child = spawn(python, ['-m', 'product_video', 'prepare-motion', source, ...(silent ? [] : ['--generate-voice'])], { cwd: path.dirname(source), windowsHide:true });
          let tail = '';
          const record = (data: Buffer) => { tail = (tail + data.toString()).slice(-3000); const lines = tail.trim().split('\n'); current.message = lines[lines.length - 1] || current.message; };
          child.stdout.on('data', record); child.stderr.on('data', record);
          const shutdown = () => child.kill('SIGINT');
          server.httpServer?.once('close', shutdown);
          const timer = setTimeout(() => { current.message = '配音任务超时，已停止；已完成缓存保留。'; child.kill('SIGTERM'); setTimeout(() => { if (child.exitCode === null) child.kill('SIGKILL'); }, 10000).unref(); }, 3_600_000);
          child.on('error', error => { clearTimeout(timer); current.status = 'error'; current.message = error.message; });
          child.on('close', code => {
            clearTimeout(timer); server.httpServer?.removeListener('close', shutdown);
            if (code !== 0) { current.status = 'error'; return; }
            try {
              const output = path.resolve(path.dirname(source), config.output ?? 'output');
              const generated = JSON.parse(readFileSync(path.join(output, 'studio.json'), 'utf8'));
              if (path.resolve(generated.directory) !== path.resolve(studio)) copyTree(path.join(generated.directory, 'public'), path.join(studio, 'public'));
              const project = JSON.parse(readFileSync(generated.project, 'utf8'));
              writeFileSync(path.join(studio, 'project.json'), JSON.stringify(project, null, 2));
              current.revision = revisionOf(readFileSync(source, 'utf8')); current.project = project; current.status = 'done'; current.message = silent ? '字幕和镜头时间轴已更新；未调用配音服务。' : '旁白、字幕和镜头时间轴已更新。';
            } catch (error) { current.status = 'error'; current.message = `配音已完成，时间轴加载失败：${String(error)}`; }
          });
          send(202, { status: 'running' });
        } catch (error) { send(400, { error: String(error) }); }
      });
    });
  } };
}
